"""End-to-end pipeline test with a mocked LLM.

The LLM returns a canned decomposition JSON. We assert the candidate
shape and that the edge gate behaves at the boundary.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import forecaster
from risk import EDGE_THRESHOLD


@dataclass
class _Q:
    best_bid: str
    best_ask: str
    volume_24h: str = "1000"
    ts: datetime = None


@dataclass
class _M:
    market_id: str
    question: str
    description: str = ""
    resolution_criteria: str = ""
    topic: str = ""
    quote: _Q = None
    resolution_time: datetime = None


def _market(bid: float, ask: float, q: str = "Will it rain tomorrow?"):
    now = datetime.now(timezone.utc)
    return _M(
        market_id="mkt-1",
        question=q,
        description="A test market about tomorrow's weather.",
        resolution_criteria="YES if more than 1mm of rainfall is recorded.",
        topic="weather",
        quote=_Q(best_bid=str(bid), best_ask=str(ask), ts=now),
        resolution_time=now + timedelta(hours=24),
    )


def _canned_client(p_yes: float, should_shrink: bool = False, source_quality: float = 0.6):
    payload = {
        "base_rate": 0.5,
        "base_rate_rationale": "test base rate",
        "evidence_for_yes": ["test evidence yes"],
        "evidence_for_no": ["test evidence no"],
        "stale_or_weak_evidence": [],
        "key_uncertainties": ["test uncertainty"],
        "time_to_resolution_risk": "medium",
        "source_quality": source_quality,
        "raw_p_yes_before_market": p_yes,
        "should_shrink": should_shrink,
        "shrink_reason": "test" if should_shrink else "",
        "final_rationale": "test stub",
    }
    text = json.dumps(payload)

    def call(system: str, user: str) -> str:  # noqa: ARG001
        return text
    return call


def test_pipeline_produces_full_candidate_shape():
    market = _market(bid=0.40, ask=0.45)
    clients = {
        "triage": _canned_client(0.55),
        "strong": _canned_client(0.62),
        "_specs": {"triage": "mock/triage", "strong": "mock/strong"},
    }
    out = forecaster.forecast(market, llm_clients=clients)
    for key in (
        "p_final", "p_market", "p_model_raw", "p_model_shrunk",
        "ensemble_probs", "disagreement_stdev",
        "yes_edge", "no_edge", "alpha_vs_market",
        "executable_edge_yes", "edge_passes_gate",
        "domain", "horizon_hours", "resolution_type",
        "evidence_retrieved", "evidence_quality", "evidence_sources",
        "decomposition_json", "confidence_bucket",
        "probability_bucket", "prompt_hash",
        "model_provider", "model", "cost_estimate_usd",
        "skip_reason_detailed",
    ):
        assert key in out, f"missing key {key}"
    assert 0.0 < out["p_final"] < 1.0
    assert out["domain"] == "weather"


def test_edge_gate_boundary_buy():
    # Market at 0.40 ask. Model says 0.85. With evidence_quality from the
    # stub the blended forecast should still produce a yes_edge above the
    # 0.08 gate -> edge_passes_gate True.
    market = _market(bid=0.38, ask=0.40)
    clients = {
        "triage": _canned_client(0.85),
        "strong": _canned_client(0.88),
        "_specs": {"triage": "mock/triage", "strong": "mock/strong"},
    }
    out = forecaster.forecast(market, llm_clients=clients)
    assert out["edge_passes_gate"] is True
    assert out["yes_edge"] >= EDGE_THRESHOLD


def test_edge_gate_boundary_skip():
    # Market is fair (0.49 / 0.51) and model agrees -> no edge, gate fails.
    market = _market(bid=0.49, ask=0.51)
    clients = {
        "triage": _canned_client(0.50),
        "strong": _canned_client(0.50),
        "_specs": {"triage": "mock/triage", "strong": "mock/strong"},
    }
    out = forecaster.forecast(market, llm_clients=clients)
    assert out["edge_passes_gate"] is False
    assert out["skip_reason_detailed"] is not None


def test_pipeline_handles_decomposition_parse_failure():
    market = _market(bid=0.45, ask=0.55)

    def bad_client(system: str, user: str) -> str:  # noqa: ARG001
        return "not json at all"

    clients = {
        "triage": bad_client,
        "strong": bad_client,
        "_specs": {"triage": "mock/triage", "strong": "mock/strong"},
    }
    out = forecaster.forecast(market, llm_clients=clients)
    # Should not crash, should fall back to a value near the market price.
    assert 0.0 < out["p_final"] < 1.0


def test_no_llm_clients_falls_back_to_market():
    market = _market(bid=0.30, ask=0.34)
    out = forecaster.forecast(market, llm_clients={})
    # Without LLM clients the pipeline still produces a candidate.
    assert "p_final" in out
    # Without any model view the forecast should sit near the market.
    assert abs(out["p_final"] - out["p_market"]) < 0.1
