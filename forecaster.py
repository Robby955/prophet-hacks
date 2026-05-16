"""Forecasting layer.

Each forecaster takes a single ``MarketData`` (from ai-prophet-core) and
returns a structured prediction dict:

    {
        "p_yes": float,                   # clamped to [P_YES_MIN, P_YES_MAX]
        "probability_bucket": float,      # nearest bucket from BUCKETS
        "confidence_note": str,
        "evidence": list[str],            # citation urls or short refs
        "uncertainty": str,
        "prompt_hash": str,
        "model_provider": str,
        "model": str,
        "cost_estimate_usd": float,
    }

Variants:
- baseline-market-price: market mid as p_yes. No model call. Control.
- model-forecast-no-retrieval: single LLM call on title/desc/quote only.
- model-forecast-retrieval: (stub) retrieval-augmented forecast.
- calibrated-ensemble: two-model agreement gate.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from risk import P_YES_MAX, P_YES_MIN, clamp_p_yes


log = logging.getLogger("prophet-hacks.forecaster")

BUCKETS: list[float] = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]

# Cost estimates per 1K tokens (rough, for logging only)
_COST_PER_1K: dict[str, float] = {
    "gpt-5.4-mini": 0.00015,
    "gpt-5.4-nano": 0.00010,
    "gpt-5.5": 0.005,
    "gpt-5.5-pro": 0.010,
    "claude-opus-4-7": 0.015,
    "claude-sonnet-4-6": 0.003,
    "claude-haiku-4-5-20251001": 0.00025,
}


def _to_float(decimal_like: Any) -> float:
    """ai-prophet-core ships prices as strings; coerce for arithmetic."""
    if decimal_like is None:
        return 0.0
    return float(decimal_like)


def bucket(p: float) -> float:
    """Nearest probability bucket. Ties round down."""
    return min(BUCKETS, key=lambda b: (abs(b - p), b))


def implied_market_probability(market) -> float:
    """Mid-price of the YES side as the market-implied YES probability.

    ``market`` is an ai_prophet_core.MarketData with ``.quote.best_bid``
    and ``.quote.best_ask`` as decimal strings in [0, 1].
    """
    bid = _to_float(market.quote.best_bid)
    ask = _to_float(market.quote.best_ask)
    return (bid + ask) / 2.0


def yes_edge(p_yes: float, yes_ask: float) -> float:
    return p_yes - yes_ask


def no_edge(p_yes: float, no_ask: float) -> float:
    return (1.0 - p_yes) - no_ask


def _hash_prompt(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rate = _COST_PER_1K.get(model, 0.001)
    return rate * (prompt_tokens + completion_tokens) / 1000.0


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a calibrated probabilistic forecaster for prediction markets.

Your task: estimate the probability that the event described resolves YES.

Rules:
- Output ONLY valid JSON: {"p_yes": <float>, "rationale": "<string>"}
- p_yes must be between 0.01 and 0.99
- Use the calibration scale:
  0.50 = no view (default when uncertain)
  0.60 = slight lean, weak evidence
  0.70 = real view, concrete reasoning, multiple signals
  0.80 = strong evidence, clear mechanism
  0.90 = near-certain, authoritative source
- Never output 0.99 or 0.01 unless the event is mechanically determined
- Be honest about uncertainty. Prefer 0.50 over fake precision.
- The rationale should be 1-3 sentences explaining your reasoning.
- Consider: base rates, current evidence, time to resolution, market context
"""


def _build_user_prompt(market) -> str:
    """Construct the user prompt from market fields."""
    bid = _to_float(market.quote.best_bid)
    ask = _to_float(market.quote.best_ask)
    mid = (bid + ask) / 2.0

    # Resolution time context
    res_time = market.resolution_time
    now = datetime.now(timezone.utc)
    if res_time.tzinfo is None:
        res_time = res_time.replace(tzinfo=timezone.utc)
    hours_to_resolution = max(0, (res_time - now).total_seconds() / 3600)

    parts = [
        f"MARKET QUESTION: {market.question}",
    ]

    if market.description:
        # Truncate long descriptions to save tokens
        desc = market.description[:800]
        if len(market.description) > 800:
            desc += "..."
        parts.append(f"\nDESCRIPTION: {desc}")

    if market.topic:
        parts.append(f"\nTOPIC: {market.topic}")

    if market.source:
        parts.append(f"SOURCE PLATFORM: {market.source}")

    parts.append(f"\nMARKET DATA:")
    parts.append(f"  Current YES bid: {bid:.3f}")
    parts.append(f"  Current YES ask: {ask:.3f}")
    parts.append(f"  Market-implied probability (mid): {mid:.3f}")
    parts.append(f"  Hours to resolution: {hours_to_resolution:.1f}")
    parts.append(f"  24h volume: ${_to_float(market.quote.volume_24h):.0f}")

    parts.append(
        "\nProvide your probability estimate as JSON: "
        '{"p_yes": <float>, "rationale": "<string>"}'
    )

    return "\n".join(parts)


def _parse_forecast_json(text: str) -> dict[str, Any]:
    """Extract p_yes and rationale from model output.

    Tries JSON parsing first, then regex fallback for noisy outputs.
    """
    # Try direct JSON parse
    try:
        data = json.loads(text.strip())
        if "p_yes" in data:
            return data
    except json.JSONDecodeError:
        pass

    # Try to find JSON block in the text
    json_match = re.search(r'\{[^{}]*"p_yes"\s*:\s*[\d.]+[^{}]*\}', text)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if "p_yes" in data:
                return data
        except json.JSONDecodeError:
            pass

    # Regex fallback: extract p_yes number
    p_match = re.search(r'"?p_yes"?\s*[:=]\s*([\d.]+)', text)
    if p_match:
        return {
            "p_yes": float(p_match.group(1)),
            "rationale": text[:200],
        }

    raise ValueError(f"Could not parse forecast from model output: {text[:200]}")


# ---------------------------------------------------------------------------
# Model callers
# ---------------------------------------------------------------------------

def _call_openai(model: str, system: str, user: str) -> tuple[str, int, int]:
    """Call OpenAI and return (response_text, prompt_tokens, completion_tokens)."""
    import openai

    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=300,
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content or ""
    usage = resp.usage
    return (
        text,
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
    )


def _call_anthropic(model: str, system: str, user: str) -> tuple[str, int, int]:
    """Call Anthropic and return (response_text, prompt_tokens, completion_tokens)."""
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=300,
    )
    text = resp.content[0].text if resp.content else ""
    return (
        text,
        resp.usage.input_tokens if resp.usage else 0,
        resp.usage.output_tokens if resp.usage else 0,
    )


def _resolve_model(config_model: str) -> tuple[str, str]:
    """Parse 'provider/model-name' into (provider, model_name).

    If no provider prefix, infer from model name.
    """
    if "/" in config_model:
        provider, model_name = config_model.split("/", 1)
        return provider, model_name

    if config_model.startswith("claude"):
        return "anthropic", config_model
    if config_model.startswith("gpt"):
        return "openai", config_model

    return "openai", config_model


def _call_model(config_model: str, system: str, user: str) -> tuple[str, str, str, int, int]:
    """Call the configured model. Returns (text, provider, model, prompt_tok, comp_tok)."""
    provider, model_name = _resolve_model(config_model)

    if provider == "openai":
        text, pt, ct = _call_openai(model_name, system, user)
    elif provider == "anthropic":
        text, pt, ct = _call_anthropic(model_name, system, user)
    else:
        raise ValueError(f"unknown model provider: {provider}")

    return text, provider, model_name, pt, ct


# ---------------------------------------------------------------------------
# Variant: baseline-market-price
# ---------------------------------------------------------------------------

def forecast_baseline_market_price(market, **_kwargs) -> dict[str, Any]:
    """Baseline: use the market mid as the forecast. No model call.

    By construction yes_edge == 0 here, so this variant should never trade.
    It exists as a control to verify the plumbing (claim, intent submission,
    finalize) end-to-end without any LLM cost.
    """
    p_raw = implied_market_probability(market)
    p = clamp_p_yes(p_raw)
    return {
        "p_yes": p,
        "probability_bucket": bucket(p),
        "confidence_note": "baseline: market mid as forecast",
        "evidence": [],
        "uncertainty": "n/a (no model view)",
        "prompt_hash": _hash_prompt("baseline-market-price", market.market_id),
        "model_provider": "none",
        "model": "baseline-market-price",
        "cost_estimate_usd": 0.0,
    }


# ---------------------------------------------------------------------------
# Variant: model-forecast-no-retrieval
# ---------------------------------------------------------------------------

_FORECAST_MODEL: str | None = None


def _get_forecast_model() -> str:
    """Read forecast model from config or env. Cached after first call."""
    global _FORECAST_MODEL
    if _FORECAST_MODEL is not None:
        return _FORECAST_MODEL

    # Check env override first
    env_model = os.getenv("PROPHET_FORECAST_MODEL")
    if env_model:
        _FORECAST_MODEL = env_model
        return _FORECAST_MODEL

    # Default: use Anthropic for forecast (better calibration in practice)
    _FORECAST_MODEL = "anthropic/claude-sonnet-4-6"
    return _FORECAST_MODEL


def forecast_model_no_retrieval(market, **_kwargs) -> dict[str, Any]:
    """Single-model LLM forecast. Title/question/quote/description only.

    No retrieval. The model sees only what the SDK provides.
    """
    config_model = _get_forecast_model()
    user_prompt = _build_user_prompt(market)
    prompt_hash = _hash_prompt("model-no-retrieval", config_model, user_prompt)

    try:
        text, provider, model_name, pt, ct = _call_model(
            config_model, SYSTEM_PROMPT, user_prompt,
        )
        parsed = _parse_forecast_json(text)
        p_raw = float(parsed["p_yes"])
        p = clamp_p_yes(p_raw)
        rationale = parsed.get("rationale", "")

        return {
            "p_yes": p,
            "probability_bucket": bucket(p),
            "confidence_note": rationale[:300],
            "evidence": [],
            "uncertainty": "model-only, no retrieval",
            "prompt_hash": prompt_hash,
            "model_provider": provider,
            "model": model_name,
            "cost_estimate_usd": _estimate_cost(model_name, pt, ct),
        }

    except Exception as e:
        log.warning("model forecast failed for %s: %s", market.market_id, e)
        # Fallback to market mid so we never crash the tick loop
        return forecast_baseline_market_price(market)


# ---------------------------------------------------------------------------
# Variant: model-forecast-retrieval (stub for now)
# ---------------------------------------------------------------------------

def forecast_model_with_retrieval(market, **_kwargs) -> dict[str, Any]:
    raise NotImplementedError(
        "Plug retrieval-augmented forecast here. Retrieval is disabled by "
        "default in config.yaml. When enabled, populate the ``evidence`` "
        "field with citation urls and adjust ``uncertainty`` accordingly.",
    )


# ---------------------------------------------------------------------------
# Variant: calibrated-ensemble (two-model agreement gate)
# ---------------------------------------------------------------------------

_TRIAGE_MODEL: str | None = None


def _get_triage_model() -> str:
    global _TRIAGE_MODEL
    if _TRIAGE_MODEL is not None:
        return _TRIAGE_MODEL

    env_model = os.getenv("PROPHET_TRIAGE_MODEL")
    if env_model:
        _TRIAGE_MODEL = env_model
        return _TRIAGE_MODEL

    _TRIAGE_MODEL = "openai/gpt-5.4-mini"
    return _TRIAGE_MODEL


_CONVICTION_THRESHOLD: float = 0.10
# Float-precision pad. abs(0.60 - 0.5) evaluates to 0.09999999999999998,
# so a naive `< 0.10` would reject the exact-bucket case. Codex Goal 3
# spec is `>= 0.10`, so the boundary at 0.10 must be admitted.
_CONVICTION_EPSILON: float = 1e-9


def agreement_gate(p_a: float, p_b: float) -> float | None:
    """Return averaged forecast only when both models agree.

    Agreement means:
    - Both above 0.5 or both below 0.5 (same direction)
    - Both have |p - 0.5| >= 0.10 (meaningful conviction)

    Returns None to signal SKIP.
    """
    # Check direction agreement
    a_yes = p_a > 0.5
    b_yes = p_b > 0.5
    if a_yes != b_yes:
        return None  # direction conflict

    # Check magnitude (conviction threshold). EPSILON pad on the LHS so
    # exact-threshold inputs like 0.60 and 0.40 (which round-trip through
    # float as 0.5 +/- 0.0999...98) are admitted, matching the >= spec.
    if (abs(p_a - 0.5) + _CONVICTION_EPSILON < _CONVICTION_THRESHOLD
            or abs(p_b - 0.5) + _CONVICTION_EPSILON < _CONVICTION_THRESHOLD):
        return None  # one model too uncertain

    return (p_a + p_b) / 2.0


def forecast_calibrated_ensemble(market, **_kwargs) -> dict[str, Any]:
    """Two-model agreement gate.

    Calls both triage (cheap) and forecast (strong) models. Trades only
    when both agree on direction and have meaningful conviction.
    """
    triage_model = _get_triage_model()
    forecast_model = _get_forecast_model()
    user_prompt = _build_user_prompt(market)

    prompt_hash = _hash_prompt(
        "calibrated-ensemble", triage_model, forecast_model, user_prompt,
    )

    try:
        # Call triage (cheap) model
        text_a, prov_a, mod_a, pt_a, ct_a = _call_model(
            triage_model, SYSTEM_PROMPT, user_prompt,
        )
        parsed_a = _parse_forecast_json(text_a)
        p_a = clamp_p_yes(float(parsed_a["p_yes"]))

        # Call forecast (strong) model
        text_b, prov_b, mod_b, pt_b, ct_b = _call_model(
            forecast_model, SYSTEM_PROMPT, user_prompt,
        )
        parsed_b = _parse_forecast_json(text_b)
        p_b = clamp_p_yes(float(parsed_b["p_yes"]))

        # Agreement gate
        agreed_p = agreement_gate(p_a, p_b)

        total_cost = (
            _estimate_cost(mod_a, pt_a, ct_a)
            + _estimate_cost(mod_b, pt_b, ct_b)
        )

        if agreed_p is None:
            # Disagreement: fall back to market mid (SKIP in practice)
            p_mid = implied_market_probability(market)
            p = clamp_p_yes(p_mid)
            return {
                "p_yes": p,
                "probability_bucket": bucket(p),
                "confidence_note": (
                    f"ensemble SKIP: {mod_a}={p_a:.3f} vs {mod_b}={p_b:.3f} "
                    f"(disagreement or low conviction)"
                ),
                "evidence": [],
                "uncertainty": "ensemble disagreement, using market mid",
                "prompt_hash": prompt_hash,
                "model_provider": f"{prov_a}+{prov_b}",
                "model": f"{mod_a}+{mod_b}",
                "cost_estimate_usd": total_cost,
            }

        p = clamp_p_yes(agreed_p)
        return {
            "p_yes": p,
            "probability_bucket": bucket(p),
            "confidence_note": (
                f"ensemble AGREE: {mod_a}={p_a:.3f}, {mod_b}={p_b:.3f}, "
                f"avg={agreed_p:.3f}. "
                f"A: {parsed_a.get('rationale', '')[:100]} | "
                f"B: {parsed_b.get('rationale', '')[:100]}"
            ),
            "evidence": [],
            "uncertainty": "ensemble agreement",
            "prompt_hash": prompt_hash,
            "model_provider": f"{prov_a}+{prov_b}",
            "model": f"{mod_a}+{mod_b}",
            "cost_estimate_usd": total_cost,
        }

    except Exception as e:
        log.warning("ensemble forecast failed for %s: %s", market.market_id, e)
        return forecast_baseline_market_price(market)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

VARIANTS: dict[str, Any] = {
    "baseline-market-price": forecast_baseline_market_price,
    "model-forecast-no-retrieval": forecast_model_no_retrieval,
    "model-forecast-retrieval": forecast_model_with_retrieval,
    "calibrated-ensemble": forecast_calibrated_ensemble,
}


def forecast(market, variant: str) -> dict[str, Any]:
    """Dispatch to the configured variant."""
    if variant not in VARIANTS:
        raise ValueError(
            f"unknown variant {variant!r}; valid: {sorted(VARIANTS)}",
        )
    return VARIANTS[variant](market)


__all__ = [
    "BUCKETS",
    "P_YES_MIN",
    "P_YES_MAX",
    "VARIANTS",
    "bucket",
    "implied_market_probability",
    "yes_edge",
    "no_edge",
    "forecast",
    "forecast_baseline_market_price",
    "forecast_model_no_retrieval",
    "forecast_calibrated_ensemble",
    "agreement_gate",
]
