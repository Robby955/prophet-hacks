"""Calibrator: math primitives + market/model blending.

Pure functions. No I/O. No LLM calls. The code computes the final probability;
no model is allowed to do that step.
"""
from __future__ import annotations

import math
from typing import Optional


# Numerical guardrails. Inputs are clamped before logit() to avoid +/- inf.
_EPS = 1e-6


def _clamp_prob(p: float, eps: float = _EPS) -> float:
    if p < eps:
        return eps
    if p > 1.0 - eps:
        return 1.0 - eps
    return p


def logit(p: float) -> float:
    """Log-odds of a probability. Input is clamped to (eps, 1-eps)."""
    q = _clamp_prob(p)
    return math.log(q / (1.0 - q))


def sigmoid(x: float) -> float:
    """Inverse of logit. Numerically stable for large |x|."""
    if x >= 0:
        ex = math.exp(-x)
        return 1.0 / (1.0 + ex)
    ex = math.exp(x)
    return ex / (1.0 + ex)


def shrink(p: float, tau: float = 0.75, anchor: Optional[float] = None) -> float:
    """Pull a raw forecast partway toward an anchor.

    tau in [0, 1]: 0 returns the anchor, 1 returns p unchanged.
    anchor default is 0.5 (max-entropy prior).
    """
    if anchor is None:
        anchor = 0.5
    if tau <= 0.0:
        return anchor
    if tau >= 1.0:
        return p
    return anchor + tau * (p - anchor)


def blend_forecast(
    p_market: float,
    p_model: float,
    evidence_quality: float,
) -> float:
    """Blend market price and model forecast with evidence-weighted pooling.

    Evidence-quality scaling controls how much the model is allowed to move
    away from the market. With zero evidence the model gets a 0.20 baseline
    weight; with maximum evidence it gets 0.65.

    A final tau-shrink toward the market price keeps overconfident model
    outputs from blowing up our edge calculation.
    """
    eq = max(0.0, min(1.0, evidence_quality))
    w_model = 0.20 + 0.45 * eq
    w_market = 1.0 - w_model
    pooled = sigmoid(w_market * logit(p_market) + w_model * logit(p_model))
    tau = 0.75
    return p_market + tau * (pooled - p_market)


def confidence_bucket(p: float) -> str:
    """Map a probability to a coarse confidence label.

    low:    |p - 0.5| <  0.10
    medium: 0.10 <= |p - 0.5| < 0.25
    high:   |p - 0.5| >= 0.25
    """
    delta = abs(p - 0.5)
    if delta < 0.10:
        return "low"
    if delta < 0.25:
        return "medium"
    return "high"


__all__ = [
    "logit",
    "sigmoid",
    "shrink",
    "blend_forecast",
    "confidence_bucket",
]
