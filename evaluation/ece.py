"""Expected Calibration Error and reliability-diagram data.

Calibration is separate from sharpness — a model can be well-calibrated
but uninformative (always predict 0.5) or sharp but miscalibrated
(always 0.9, half the time wrong). The Prophet Hacks strategy says
to log BOTH Brier and ECE because they measure different things.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


def expected_calibration_error(
    probs: Sequence[float],
    outcomes: Sequence[int],
    n_bins: int = 10,
) -> float:
    """Equal-width-bin ECE.

    ECE = sum_k (n_k / N) * | p_bar_k - o_bar_k |
    Lower is better; 0.0 is perfect calibration.
    """
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must have the same length")
    n = len(probs)
    if n == 0:
        return 0.0

    bins = [[] for _ in range(n_bins)]
    bin_outcomes = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append(p)
        bin_outcomes[idx].append(o)

    ece = 0.0
    for ps, os in zip(bins, bin_outcomes):
        if not ps:
            continue
        n_k = len(ps)
        p_bar = sum(ps) / n_k
        o_bar = sum(os) / n_k
        ece += (n_k / n) * abs(p_bar - o_bar)
    return ece


def maximum_calibration_error(
    probs: Sequence[float],
    outcomes: Sequence[int],
    n_bins: int = 10,
) -> float:
    """Worst-bin calibration gap. Highlights single-bucket failures
    that ECE might smooth over."""
    if len(probs) != len(outcomes) or not probs:
        return 0.0

    bins = [[] for _ in range(n_bins)]
    bin_outcomes = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append(p)
        bin_outcomes[idx].append(o)

    worst = 0.0
    for ps, os in zip(bins, bin_outcomes):
        if not ps:
            continue
        gap = abs(sum(ps) / len(ps) - sum(os) / len(os))
        if gap > worst:
            worst = gap
    return worst


@dataclass(frozen=True)
class ReliabilityBin:
    center: float
    p_mean: float
    outcome_mean: float
    count: int


def reliability_diagram_data(
    probs: Sequence[float],
    outcomes: Sequence[int],
    n_bins: int = 10,
) -> list[ReliabilityBin]:
    """Per-bin data for a reliability diagram. Use the output to render
    a calibration curve in the live monitor or post-event report.
    """
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must have the same length")
    bins = [[] for _ in range(n_bins)]
    bin_outcomes = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append(p)
        bin_outcomes[idx].append(o)

    out = []
    for k, (ps, os) in enumerate(zip(bins, bin_outcomes)):
        center = (k + 0.5) / n_bins
        if not ps:
            out.append(ReliabilityBin(center=center, p_mean=center, outcome_mean=center, count=0))
            continue
        p_mean = sum(ps) / len(ps)
        o_mean = sum(os) / len(os)
        out.append(
            ReliabilityBin(center=center, p_mean=p_mean, outcome_mean=o_mean, count=len(ps))
        )
    return out
