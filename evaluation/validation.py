"""Validation helpers for binary forecast evaluation metrics."""

from __future__ import annotations

import math
from typing import Sequence


def validate_probability(value: float, *, name: str = "probability") -> float:
    try:
        p = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number in [0, 1]") from exc
    if not math.isfinite(p) or p < 0.0 or p > 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return p


def validate_outcome(value: int, *, name: str = "outcome") -> int:
    if value not in (0, 1):
        raise ValueError(f"{name} must be 0 or 1")
    return int(value)


def validate_n_bins(n_bins: int) -> int:
    if isinstance(n_bins, bool) or not isinstance(n_bins, int) or n_bins <= 0:
        raise ValueError("n_bins must be a positive integer")
    return n_bins


def validate_binary_series(
    probs: Sequence[float],
    outcomes: Sequence[int],
) -> tuple[list[float], list[int]]:
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must have the same length")
    return (
        [
            validate_probability(p, name=f"probs[{i}]")
            for i, p in enumerate(probs)
        ],
        [
            validate_outcome(o, name=f"outcomes[{i}]")
            for i, o in enumerate(outcomes)
        ],
    )


def probability_bin_index(p: float, n_bins: int) -> int:
    """Map a validated probability to an equal-width bin index."""
    return min(int(p * n_bins), n_bins - 1)
