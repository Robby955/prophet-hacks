"""Ensemble combiner over multiple model forecasts.

The standard combination is median-of-logits then sigmoid back. Median is
robust to a single outlier model; logit space puts equal "distance"
between e.g. 0.5->0.6 and 0.9->0.95.

Disagreement is the population standard deviation of the model forecasts
(in probability space, not logit space). High disagreement triggers a hard
shrink toward the market or an abstain.
"""
from __future__ import annotations

import statistics
from typing import Iterable, Sequence

from calibrator import logit, sigmoid


# Hard cut-off above which we shrink to the market or abstain.
DISAGREEMENT_THRESHOLD: float = 0.12

# Edge gate used to decide whether the cheap model triggers a strong call.
# Mirrors risk.EDGE_THRESHOLD; kept local so this module has no risk dep.
EDGE_GATE: float = 0.08

# A second strong model is only worth its cost when the first strong model
# is well past the edge gate. 1.5x the gate by default.
SECOND_STRONG_MULTIPLIER: float = 1.5

# Defaults from the locked v2 spec. Override via env or by passing a list
# directly to ensemble_prob().
DEFAULT_TRIAGE_MODEL = "openai/gpt-5.4-mini"
DEFAULT_STRONG_MODEL = "anthropic/claude-opus-4-7"
DEFAULT_OPTIONAL_STRONG_MODEL = "anthropic/claude-sonnet-4-6"


def ensemble_prob(probs: Sequence[float]) -> float:
    """Median-of-logits combiner.

    Empty input returns 0.5 (no view). One input passes through.
    """
    xs = [float(p) for p in probs]
    if not xs:
        return 0.5
    if len(xs) == 1:
        return xs[0]
    return sigmoid(statistics.median([logit(p) for p in xs]))


def disagreement_penalty(probs: Iterable[float]) -> float:
    """Population stdev of model forecasts in probability space."""
    xs = [float(p) for p in probs]
    if len(xs) < 2:
        return 0.0
    return statistics.pstdev(xs)


def should_call_strong(p_triage: float, market_p: float, gate: float = EDGE_GATE) -> bool:
    """Promote a market to the strong model only when the triage forecast
    shows enough edge versus the market to justify the cost."""
    return abs(p_triage - market_p) >= gate


def should_call_second_strong(
    p_first_strong: float,
    market_p: float,
    gate: float = EDGE_GATE,
    multiplier: float = SECOND_STRONG_MULTIPLIER,
) -> bool:
    """Optional second strong model fires only when the first crossed the
    gate by at least the configured multiplier."""
    return abs(p_first_strong - market_p) >= gate * multiplier


def combine_with_disagreement(
    probs: Sequence[float],
    market_p: float,
    threshold: float = DISAGREEMENT_THRESHOLD,
) -> dict:
    """Run the full ensemble combiner with disagreement gating.

    Returns a dict with the raw probs, the median forecast, the stdev,
    whether the disagreement gate fired, and the recommended final
    probability before the calibrator step. Caller decides whether to
    pass the result to calibrator.blend_forecast or to abstain.
    """
    p_med = ensemble_prob(probs)
    stdev = disagreement_penalty(probs)
    high_disagreement = stdev > threshold
    if high_disagreement:
        # Hard shrink toward the market when the models disagree.
        recommended = market_p + 0.25 * (p_med - market_p)
    else:
        recommended = p_med
    return {
        "probs": list(probs),
        "p_median": p_med,
        "stdev": stdev,
        "high_disagreement": high_disagreement,
        "recommended": recommended,
    }


__all__ = [
    "DISAGREEMENT_THRESHOLD",
    "EDGE_GATE",
    "SECOND_STRONG_MULTIPLIER",
    "DEFAULT_TRIAGE_MODEL",
    "DEFAULT_STRONG_MODEL",
    "DEFAULT_OPTIONAL_STRONG_MODEL",
    "ensemble_prob",
    "disagreement_penalty",
    "should_call_strong",
    "should_call_second_strong",
    "combine_with_disagreement",
]
