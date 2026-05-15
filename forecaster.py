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

Skeleton: only the baseline (market mid as p_yes) is wired. Other variants
raise NotImplementedError pointing at where to plug them in.
"""
from __future__ import annotations

import hashlib
from typing import Any

from risk import P_YES_MAX, P_YES_MIN, clamp_p_yes


BUCKETS: list[float] = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]


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


def forecast_baseline_market_price(market) -> dict[str, Any]:
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


def forecast_model_no_retrieval(market) -> dict[str, Any]:
    raise NotImplementedError(
        "Plug a single-model LLM forecast here. Construct a prompt from "
        "market.question, market.description, market.resolution_time. "
        "Parse JSON {p_yes, rationale}. Clamp via risk.clamp_p_yes. "
        "See forecaster.forecast_baseline_market_price for the return shape.",
    )


def forecast_model_with_retrieval(market) -> dict[str, Any]:
    raise NotImplementedError(
        "Plug retrieval-augmented forecast here. Retrieval is disabled by "
        "default in config.yaml. When enabled, populate the ``evidence`` "
        "field with citation urls and adjust ``uncertainty`` accordingly.",
    )


def forecast_calibrated_ensemble(market) -> dict[str, Any]:
    raise NotImplementedError(
        "Plug calibrated ensemble here. Combine multiple model forecasts via "
        "isotonic calibration or simple averaging. Document the calibration "
        "set in SUBMISSION_NOTES.md before enabling.",
    )


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
]
