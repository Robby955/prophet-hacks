"""Forecaster: v2 calibrated-ensemble pipeline.

Seven-stage composed pipeline:

    market_router -> retrieval_gate -> decomposition_forecaster
                  -> ensemble_forecaster -> calibrator -> risk_gate

Each stage is a pure-ish function that takes the working candidate dict
and returns it enriched. The candidate dict is the single source of truth
for everything downstream needs to log a decision.

The locked thesis: market-aware, retrieval-disciplined, calibrated ensemble.
The code, not the model, computes the final probability.

Safety: the top-level forecast() entry point wraps the pipeline in a
try/except that returns a market-mid fallback so a single stage failure
never crashes the tick loop.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Callable, Optional

import market_router
import retrieval
import decomposition
import ensemble
import calibrator
from risk import P_YES_MAX, P_YES_MIN, EDGE_THRESHOLD, clamp_p_yes


log = logging.getLogger("prophet-hacks.forecaster")

BUCKETS: list[float] = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]

# Conviction floor used by the meaningful_conviction skip gate. Carried
# over from Gemini's agreement-gate. Configurable via env so we can tune
# without a redeploy on Saturday.
MEANINGFUL_CONVICTION_FLOOR: float = float(
    os.getenv("PROPHET_CONVICTION_FLOOR", "0.10"),
)


def bucket(p: float) -> float:
    """Nearest probability bucket. Ties round down."""
    return min(BUCKETS, key=lambda b: (abs(b - p), b))


def implied_market_probability(market) -> float:
    bid = float(market.quote.best_bid)
    ask = float(market.quote.best_ask)
    return (bid + ask) / 2.0


def yes_edge(p_yes: float, yes_ask: float) -> float:
    return p_yes - yes_ask


def no_edge(p_yes: float, no_ask: float) -> float:
    return (1.0 - p_yes) - no_ask


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Default LLM clients
# ---------------------------------------------------------------------------

LLMClient = Callable[[str, str], str]


def _openai_client(model: str) -> LLMClient:
    def call(system: str, user: str) -> str:
        import openai
        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""
    return call


def _anthropic_client(model: str) -> LLMClient:
    def call(system: str, user: str) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(
            model=model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=600,
        )
        return resp.content[0].text if resp.content else ""
    return call


def _client_for(spec: str) -> LLMClient:
    """Build an LLM client from a 'provider/model' spec.

    Defaults to openai for unprefixed strings starting with 'gpt' and to
    anthropic for those starting with 'claude'.
    """
    if "/" in spec:
        provider, model = spec.split("/", 1)
    elif spec.startswith("claude"):
        provider, model = "anthropic", spec
    elif spec.startswith("gpt"):
        provider, model = "openai", spec
    else:
        provider, model = "openai", spec

    if provider == "openai":
        return _openai_client(model)
    if provider == "anthropic":
        return _anthropic_client(model)
    raise ValueError(f"unknown provider {provider!r}")


def default_llm_clients() -> dict[str, LLMClient]:
    """Build the default triage / strong / optional-strong client trio.

    Environment overrides:
        PROPHET_TRIAGE_MODEL
        PROPHET_STRONG_MODEL
        PROPHET_OPTIONAL_STRONG_MODEL
    """
    triage = os.getenv("PROPHET_TRIAGE_MODEL", ensemble.DEFAULT_TRIAGE_MODEL)
    strong = os.getenv("PROPHET_STRONG_MODEL", ensemble.DEFAULT_STRONG_MODEL)
    opt = os.getenv("PROPHET_OPTIONAL_STRONG_MODEL", ensemble.DEFAULT_OPTIONAL_STRONG_MODEL)
    return {
        "triage": _client_for(triage),
        "strong": _client_for(strong),
        "optional_strong": _client_for(opt),
        "_specs": {"triage": triage, "strong": strong, "optional_strong": opt},
    }


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def stage_market_router(candidate: dict) -> dict:
    """Stage 1. Classify the market by domain, horizon, resolution_type."""
    meta = market_router.route(candidate["market"])
    candidate["domain"] = meta["domain"]
    candidate["horizon_hours"] = meta["horizon_hours"]
    candidate["resolution_type"] = meta["resolution_type"]
    candidate["_market_meta"] = meta
    return candidate


def stage_retrieval_gate(candidate: dict) -> dict:
    """Stage 2. Decide whether to retrieve, then assemble the evidence block."""
    result = retrieval.gather_evidence(
        candidate["market"], candidate["_market_meta"],
    )
    candidate["evidence_retrieved"] = result["retrieved"]
    candidate["evidence_block"] = result["block"]
    candidate["evidence_quality"] = result["quality"]
    candidate["evidence_sources"] = [
        {"url": it.get("url"), "source_type": it.get("source_type"),
         "credibility": it.get("credibility")}
        for it in result["block"].get("items", [])
    ]
    return candidate


def stage_decomposition(candidate: dict, llm_clients: dict) -> dict:
    """Stage 3. Ask the triage model for a JSON decomposition."""
    triage = llm_clients.get("triage")
    if triage is None:
        candidate["decomposition_json"] = None
        candidate["p_triage"] = implied_market_probability(candidate["market"])
        return candidate
    parsed = decomposition.decompose(
        candidate["market"],
        candidate["evidence_block"],
        candidate["_market_meta"],
        triage,
    )
    candidate["decomposition_json"] = parsed
    candidate["p_triage"] = float(parsed["p_yes"])
    candidate["decomposition_parse_failed"] = parsed.get("parse_failed", False)
    return candidate


def stage_ensemble(candidate: dict, llm_clients: dict) -> dict:
    """Stage 4. Optionally call the strong model(s), then combine via the
    median-of-logits ensemble. Promotes only when the triage forecast
    crosses the edge gate vs the market."""
    market_p = implied_market_probability(candidate["market"])
    candidate["p_market"] = market_p
    p_triage = candidate.get("p_triage", market_p)

    probs: list[float] = [p_triage]

    strong = llm_clients.get("strong")
    if strong is not None and ensemble.should_call_strong(p_triage, market_p):
        parsed = decomposition.decompose(
            candidate["market"],
            candidate["evidence_block"],
            candidate["_market_meta"],
            strong,
        )
        p_strong = float(parsed["p_yes"])
        candidate["p_strong"] = p_strong
        candidate["decomposition_json_strong"] = parsed
        probs.append(p_strong)

        opt = llm_clients.get("optional_strong")
        if opt is not None and ensemble.should_call_second_strong(p_strong, market_p):
            parsed2 = decomposition.decompose(
                candidate["market"],
                candidate["evidence_block"],
                candidate["_market_meta"],
                opt,
            )
            probs.append(float(parsed2["p_yes"]))
            candidate["decomposition_json_optional"] = parsed2

    ens = ensemble.combine_with_disagreement(probs, market_p)
    candidate["ensemble_probs"] = ens["probs"]
    candidate["disagreement_stdev"] = ens["stdev"]
    candidate["disagreement_high"] = ens["high_disagreement"]
    candidate["p_model_raw"] = ens["recommended"]
    return candidate


def stage_calibrator(candidate: dict) -> dict:
    """Stage 5. Shrink + blend with the market price."""
    market_p = candidate["p_market"]
    p_model_raw = candidate["p_model_raw"]
    eq = candidate.get("evidence_quality", 0.0)

    p_model_shrunk = calibrator.shrink(p_model_raw, tau=0.75, anchor=market_p)
    candidate["p_model_shrunk"] = p_model_shrunk

    p_final = calibrator.blend_forecast(market_p, p_model_shrunk, eq)
    p_final = clamp_p_yes(p_final)
    candidate["p_final"] = p_final
    candidate["confidence_bucket"] = calibrator.confidence_bucket(p_final)
    # Gemini's conviction floor: trade only when the calibrated probability
    # is at least MEANINGFUL_CONVICTION_FLOOR away from 0.5. Disagreement
    # penalties in ensemble + this floor jointly replace the old agreement-gate.
    candidate["meaningful_conviction"] = (
        abs(p_final - 0.5) >= MEANINGFUL_CONVICTION_FLOOR
    )
    return candidate


def stage_risk_gate(candidate: dict) -> dict:
    """Stage 6. Compute final edges and gating signals.

    The actual order-construction lives in agent._decide_for_market; this
    stage just exposes the numbers that drive that decision.
    """
    import risk as risk_mod  # local import to avoid cycle in tests
    market = candidate["market"]
    p_final = candidate["p_final"]
    market_p = candidate["p_market"]
    yes_ask = float(market.quote.best_ask)
    bid = float(market.quote.best_bid)
    no_ask = 1.0 - bid

    candidate["yes_edge"] = yes_edge(p_final, yes_ask)
    candidate["no_edge"] = no_edge(p_final, no_ask)
    candidate["alpha_vs_market"] = risk_mod.alpha_vs_market(p_final, market_p)
    candidate["executable_edge_yes"] = risk_mod.executable_edge(
        p_final, bid=bid, ask=yes_ask
    )
    candidate["edge_passes_gate"] = (
        max(candidate["yes_edge"], candidate["no_edge"]) >= EDGE_THRESHOLD
    )
    return candidate


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------


def _market_mid_fallback(market: Any, reason: str) -> dict:
    """Safety fallback used when the pipeline raises.

    Keeps the agent loop alive: returns market mid as p_yes with the
    minimum set of fields the agent + trace writer expect.
    """
    p = clamp_p_yes(implied_market_probability(market))
    return {
        "p_yes": p,
        "p_final": p,
        "p_market": p,
        "p_model_raw": p,
        "p_model_shrunk": p,
        "probability_bucket": bucket(p),
        "confidence_note": f"pipeline fallback: {reason}",
        "uncertainty": "pipeline_failure",
        "evidence": [],
        "evidence_sources": [],
        "domain": "other",
        "yes_edge": 0.0,
        "no_edge": 0.0,
        "alpha_vs_market": 0.0,
        "executable_edge_yes": 0.0,
        "edge_passes_gate": False,
        "meaningful_conviction": False,
        "disagreement_stdev": 0.0,
        "disagreement_high": False,
        "ensemble_probs": [p],
        "prompt_hash": _hash("fallback", reason),
        "model_provider": "fallback",
        "model": "market-mid",
        "cost_estimate_usd": 0.0,
        "skip_reason_detailed": f"pipeline fallback: {reason}",
        "decomposition_json": None,
    }


def forecast(
    market: Any,
    llm_clients: Optional[dict] = None,
) -> dict:
    """Run the seven-stage pipeline and return a candidate dict.

    Wrapped end-to-end in try/except: any stage failure returns the
    market-mid fallback so the tick loop never crashes.
    """
    if llm_clients is None:
        try:
            llm_clients = default_llm_clients()
        except Exception as e:  # noqa: BLE001
            log.warning("falling back to no-LLM clients: %s", e)
            llm_clients = {}

    try:
        candidate: dict = {"market": market}

        candidate = stage_market_router(candidate)
        candidate = stage_retrieval_gate(candidate)
        candidate = stage_decomposition(candidate, llm_clients)
        candidate = stage_ensemble(candidate, llm_clients)
        candidate = stage_calibrator(candidate)
        candidate = stage_risk_gate(candidate)

        # Legacy / shared fields the logger and agent already read.
        specs = (llm_clients or {}).get("_specs", {}) if isinstance(llm_clients, dict) else {}
        candidate["p_yes"] = candidate["p_final"]
        candidate["probability_bucket"] = bucket(candidate["p_final"])
        candidate["evidence"] = [
            s.get("url") for s in candidate.get("evidence_sources", []) if s.get("url")
        ]
        confidence_note = (
            f"domain={candidate['domain']} eq={candidate['evidence_quality']:.2f} "
            f"p_market={candidate['p_market']:.3f} p_model={candidate['p_model_shrunk']:.3f} "
            f"p_final={candidate['p_final']:.3f} alpha={candidate['alpha_vs_market']:+.3f}"
        )
        candidate["confidence_note"] = confidence_note
        candidate["uncertainty"] = candidate["confidence_bucket"]
        candidate["model_provider"] = "ensemble"
        candidate["model"] = "+".join(
            [v for k, v in specs.items() if k in {"triage", "strong"}]
        ) or "v2-pipeline"
        candidate["prompt_hash"] = _hash(
            "v2",
            candidate["domain"],
            candidate["resolution_type"],
            str(candidate.get("ensemble_probs", [])),
        )
        candidate["cost_estimate_usd"] = 0.0
        candidate["skip_reason_detailed"] = _build_skip_reason(candidate)
        return candidate
    except Exception as e:  # noqa: BLE001
        log.warning(
            "forecast pipeline failed for %s: %s",
            getattr(market, "market_id", "?"), e,
        )
        return _market_mid_fallback(market, f"{type(e).__name__}: {e}")


def _build_skip_reason(candidate: dict) -> Optional[str]:
    if not candidate["edge_passes_gate"]:
        return (
            f"edge below gate: yes={candidate['yes_edge']:+.3f} "
            f"no={candidate['no_edge']:+.3f} threshold={EDGE_THRESHOLD}"
        )
    if candidate.get("disagreement_high"):
        return f"high model disagreement: stdev={candidate['disagreement_stdev']:.3f}"
    if not candidate.get("meaningful_conviction", True):
        return (
            f"conviction floor: |p_final-0.5|={abs(candidate['p_final']-0.5):.3f} "
            f"< {MEANINGFUL_CONVICTION_FLOOR}"
        )
    return None


__all__ = [
    "BUCKETS",
    "P_YES_MIN",
    "P_YES_MAX",
    "MEANINGFUL_CONVICTION_FLOOR",
    "bucket",
    "implied_market_probability",
    "yes_edge",
    "no_edge",
    "forecast",
    "stage_market_router",
    "stage_retrieval_gate",
    "stage_decomposition",
    "stage_ensemble",
    "stage_calibrator",
    "stage_risk_gate",
    "default_llm_clients",
]
