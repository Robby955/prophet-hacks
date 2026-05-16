"""Brier score, Brier skill score, Murphy decomposition.

These are the proper scoring rules locked by the Prophet Hacks
strategy: forecast quality must be measured on Brier (not on PnL
alone, which conflates calibration with market-mispricing edge).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple


def brier_score(p: float, outcome: int) -> float:
    """Single-prediction Brier loss for binary outcome.

    p in [0, 1], outcome in {0, 1}. Result in [0, 1], lower is better.
    Random baseline = 0.25.
    """
    return (p - outcome) ** 2


def mean_brier(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must have the same length")
    if not probs:
        return 0.0
    total = sum(brier_score(p, o) for p, o in zip(probs, outcomes))
    return total / len(probs)


def brier_skill_score(
    brier_model: float,
    brier_baseline: float,
) -> float:
    """Brier Skill Score (BSS) vs a baseline.

    BSS = 1 - (Brier_model / Brier_baseline)
    BSS > 0 means the model beats the baseline.
    BSS = 1 is perfect.
    BSS < 0 means worse than the baseline.

    For Prophet Hacks, the baseline is typically the market-implied
    probability — BSS > 0 means we're adding value over the market prior.
    """
    if brier_baseline <= 0:
        return float("nan")
    return 1.0 - (brier_model / brier_baseline)


@dataclass(frozen=True)
class MurphyDecomposition:
    """Murphy / Brier decomposition into reliability, resolution, uncertainty.

    Brier = reliability - resolution + uncertainty.
    Lower reliability is better. Higher resolution is better. Uncertainty
    is a property of the outcomes themselves (irreducible).
    """

    reliability: float
    resolution: float
    uncertainty: float
    brier: float


def murphy_decomposition(
    probs: Sequence[float],
    outcomes: Sequence[int],
    n_bins: int = 10,
) -> MurphyDecomposition:
    """Standard Brier decomposition with equal-width probability bins.

    reliability = mean_n( n_k * (p_bar_k - o_bar_k) ** 2 ) / N
    resolution  = mean_n( n_k * (o_bar_k - o_bar)   ** 2 ) / N
    uncertainty = o_bar * (1 - o_bar)
    """
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must have the same length")
    n = len(probs)
    if n == 0:
        return MurphyDecomposition(0.0, 0.0, 0.0, 0.0)

    o_bar = sum(outcomes) / n
    uncertainty = o_bar * (1.0 - o_bar)

    # Bin assignments
    bins = [[] for _ in range(n_bins)]
    bin_outcomes = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append(p)
        bin_outcomes[idx].append(o)

    reliability = 0.0
    resolution = 0.0
    for ps, os in zip(bins, bin_outcomes):
        if not ps:
            continue
        n_k = len(ps)
        p_bar_k = sum(ps) / n_k
        o_bar_k = sum(os) / n_k
        reliability += n_k * (p_bar_k - o_bar_k) ** 2
        resolution += n_k * (o_bar_k - o_bar) ** 2
    reliability /= n
    resolution /= n

    brier = reliability - resolution + uncertainty
    return MurphyDecomposition(
        reliability=reliability,
        resolution=resolution,
        uncertainty=uncertainty,
        brier=brier,
    )


def pnl_alpha_vs_market(
    p_final: float,
    p_market: float,
    outcome: int,
) -> float:
    """Alpha vs market on a single observation.

    alpha = brier_market - brier_model
    Positive alpha = model beat market on this market.
    """
    brier_m = brier_score(p_final, outcome)
    brier_market = brier_score(p_market, outcome)
    return brier_market - brier_m
