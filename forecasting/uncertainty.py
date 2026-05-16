"""Forecast variance estimation — combine model disagreement,
retrieval conflict, and market microstructure into one uncertainty
scalar used as a gate / shrinkage signal.

This is the "thin area indicator" in the SAE analogue: when our
forecast for this market has high variance, borrow harder from the
market prior.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import pstdev
from typing import Sequence


@dataclass(frozen=True)
class UncertaintyBreakdown:
    model_disagreement: float
    retrieval_conflict: float
    spread_width: float
    horizon_proximity: float
    aggregate: float


def model_disagreement(probs: Sequence[float]) -> float:
    """RMS spread across an LLM ensemble. Returns 0 when probs is
    empty or singleton."""
    if len(probs) < 2:
        return 0.0
    return pstdev(probs)


def normalize_spread(yes_bid: float, yes_ask: float) -> float:
    """Spread normalized to [0, 1]. A spread of 0.05 in $/share is
    moderate; > 0.20 is dangerous."""
    spread = max(0.0, yes_ask - yes_bid)
    return min(1.0, spread / 0.20)


def horizon_proximity(hours_to_resolution: float) -> float:
    """Returns 1.0 when very close to resolution (market sharpest),
    ramps to 0 as horizon extends past ~1 week.

    Combined as a *resistance* term — proximity-to-resolution
    INCREASES total uncertainty for the model (we should trust the
    market more, so deviating costs more)."""
    if hours_to_resolution < 1.0:
        return 1.0
    if hours_to_resolution >= 24 * 7:
        return 0.0
    # Log-decay: half at 24h
    import math
    return min(1.0, max(0.0, 1.0 - math.log10(1.0 + hours_to_resolution / 24.0) / 1.5))


def aggregate_uncertainty(
    *,
    model_probs: Sequence[float],
    retrieval_conflict: float,
    yes_bid: float,
    yes_ask: float,
    hours_to_resolution: float,
) -> UncertaintyBreakdown:
    """Compose the four signals into a single uncertainty scalar in [0, 1].

    Weighted sum, normalized to [0, 1]. The weights match the v2.1
    required_edge formula structure so the same forces that demand
    more edge also shrink toward the market harder.
    """
    md = model_disagreement(model_probs)
    md_norm = min(1.0, md / 0.20)
    rc = max(0.0, min(1.0, retrieval_conflict))
    sw = normalize_spread(yes_bid, yes_ask)
    hp = horizon_proximity(hours_to_resolution)

    aggregate = min(
        1.0,
        0.40 * md_norm + 0.20 * rc + 0.20 * sw + 0.20 * hp,
    )
    return UncertaintyBreakdown(
        model_disagreement=md,
        retrieval_conflict=rc,
        spread_width=sw,
        horizon_proximity=hp,
        aggregate=aggregate,
    )


def uncertainty_action(aggregate: float) -> str:
    """Three-tier gate based on aggregate uncertainty.

    Returns one of: "trust_model" | "shrink_toward_market" | "skip".
    """
    if aggregate < 0.30:
        return "trust_model"
    if aggregate < 0.60:
        return "shrink_toward_market"
    return "skip"
