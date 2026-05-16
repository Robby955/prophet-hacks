"""Market-aware blending with Kalshi-paper-informed rules.

The Kalshi paper (Rob's desktop ref, May 2026) documents a real
favorite-longshot bias in prediction markets:

  - Buyers of contracts below $0.10 lose >60% on average.
  - Contracts above $0.50 show small positive returns.
  - Makers outperform Takers; both show the longshot pattern.

LLMs love vivid low-probability narratives. The market data says
those narratives are dangerous. The rules below actively resist
LLM overreach in those regimes.

This module is experimental/offline support. Keep it out of the live path
until its assumptions are tested against a no-leakage holdout.
"""

from __future__ import annotations

import math


# -- Numeric helpers -------------------------------------------------------


def clamp(p: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return min(max(p, lo), hi)


def logit(p: float) -> float:
    p = clamp(p)
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# -- Kalshi-paper-informed rules ------------------------------------------


def kalshi_longshot_guard(
    p_market: float,
    p_model: float,
    source_quality: float,
    model_agreement: float,
) -> float:
    """Clamp the LLM's ability to move us *up* on sub-$0.10 longshots
    unless evidence is excellent.

    Empirical basis: Kalshi paper finds buyers of contracts below $0.10
    lose >60% on average. LLMs frequently overweight vivid low-probability
    narratives. Without this guard the calibrated blend would consistently
    bleed money on long-shot trades.

    Rule: if p_market < 0.10 AND the model wants to push us upward by more
    than 0.05, require source_quality > 0.7 AND model_agreement > 0.7.
    Otherwise cap the upward move at +0.05 from p_market.

    Returns the (possibly-capped) p_model.
    """
    p_market = clamp(p_market)
    p_model = clamp(p_model)
    if p_market >= 0.10:
        return p_model
    upward_move = p_model - p_market
    if upward_move <= 0.05:
        return p_model
    if source_quality > 0.7 and model_agreement > 0.7:
        return p_model
    return min(p_model, p_market + 0.05)


def favorites_no_shrink(p_market: float, p_model: float) -> float:
    """When the market thinks something is a strong favorite (>0.85),
    don't let the LLM's natural conservatism shrink us toward 0.50.

    Empirical basis: same Kalshi paper, opposite end of the spectrum:
    high-price contracts show small POSITIVE returns. LLMs trained
    on safety-tuned defaults often output more conservative numbers
    on near-certain events. The market is usually right there.

    Rule: if p_market > 0.85 AND the model is lower than the market,
    blend conservatively toward p_market (70% market / 30% model).
    Otherwise pass through.
    """
    p_market = clamp(p_market)
    p_model = clamp(p_model)
    if p_market <= 0.85:
        return p_model
    if p_model < p_market:
        return 0.7 * p_market + 0.3 * p_model
    return p_model


def longshot_proximity(p_market: float) -> float:
    """Returns 0 when p_market >= 0.10, ramps to 1.0 as p_market -> 0.

    Used by risk.required_edge to tighten the edge gate on longshots:
        required_edge += 0.05 * longshot_proximity(p_market)
    """
    p_market = max(0.0, min(1.0, p_market))
    if p_market >= 0.10:
        return 0.0
    return (0.10 - p_market) / 0.10


# -- Credibility-weighted blend (v2.1 refined formula) --------------------


def credibility(
    source_quality: float,
    model_agreement: float,
    horizon_weight: float,
) -> float:
    """Actuarial credibility weight for the LLM forecast vs the market.

    source_quality: [0, 1] — current/primary/relevant > stale/noisy.
    model_agreement: [0, 1] — how tightly the model ensemble agrees.
    horizon_weight: [0, 1] — higher when far from resolution (market
        updates slowly), lower near resolution (market is sharpest).

    Returns a credibility in [0.05, 0.75]. Capped because we never want
    the model to fully overwrite the market prior, and we never want
    to fully ignore the model either.
    """
    source_quality = max(0.0, min(1.0, source_quality))
    model_agreement = max(0.0, min(1.0, model_agreement))
    horizon_weight = max(0.0, min(1.0, horizon_weight))
    raw = 0.10 + 0.45 * source_quality + 0.25 * model_agreement + 0.20 * horizon_weight
    return max(0.05, min(0.75, raw))


def blend_market_and_model(
    *,
    p_market: float,
    p_model: float,
    source_quality: float,
    model_agreement: float,
    horizon_weight: float,
) -> float:
    """Credibility-weighted logit-space blend.

    Code computes the final probability. The model only earns weight
    when evidence + agreement + horizon justify it. See v2.1 refined
    formula in the Prophet Hacks strategy memory.
    """
    c = credibility(source_quality, model_agreement, horizon_weight)
    z_market = logit(p_market)
    z_model = logit(p_model)
    pooled = sigmoid(z_market + c * (z_model - z_market))
    return clamp(pooled)


def full_blend(
    *,
    p_market: float,
    p_model: float,
    source_quality: float,
    model_agreement: float,
    horizon_weight: float,
) -> float:
    """Compose the full v3 blend in one call: Kalshi guards then
    credibility-weighted blend.

    Order matters: longshot guard caps p_model first so the blend
    can't be pulled aggressively upward by a guarded LLM forecast.
    Favorites no-shrink applies to the FINAL p (after blend).
    """
    guarded = kalshi_longshot_guard(p_market, p_model, source_quality, model_agreement)
    blended = blend_market_and_model(
        p_market=p_market,
        p_model=guarded,
        source_quality=source_quality,
        model_agreement=model_agreement,
        horizon_weight=horizon_weight,
    )
    return favorites_no_shrink(p_market, blended)
