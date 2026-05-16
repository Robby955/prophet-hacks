"""The SAE composed estimator.

Composes market prior, domain pool, calibrated LLM signal, and
historical similar-markets signal into a single hierarchical estimate.

This is the v4 entry point that the live agent should call. It wraps:
  - `market_blend.full_blend` (v3 calibrated blend with Kalshi guards)
  - `domain_pools` (random-effects offset per domain)
  - `reliability_tracking` (per-(model, domain) trust multiplier)
  - `uncertainty.aggregate_uncertainty` (variance estimate)

Returns a structured result with the final probability plus enough
provenance to log + audit each stratum's contribution.

The implementation prefers explicit, auditable arithmetic over
abstraction. Every weight, every shrinkage decision, every floor
should be inspectable in the JSONL trace.

Cite: Rao & Molina, Small Area Estimation (2nd ed) 2015; Fay-Herriot
1979; James-Stein 1961. Rob's UVic MSc Stats thesis area.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from .market_blend import (
    blend_market_and_model,
    clamp,
    credibility,
    favorites_no_shrink,
    kalshi_longshot_guard,
    logit,
    longshot_proximity,
    sigmoid,
)
from .reliability_tracking import ReliabilityTracker
from .uncertainty import UncertaintyBreakdown, aggregate_uncertainty


@dataclass
class BorrowedStrengthInputs:
    p_market: float
    p_models: list  # list[(model_name, p_yes)]
    domain: str
    source_quality: float
    retrieval_conflict: float
    hours_to_resolution: float
    yes_bid: float
    yes_ask: float
    p_history: Optional[float] = None  # historical similar-markets prior
    n_history: int = 0


@dataclass
class BorrowedStrengthResult:
    p_final: float
    p_market: float
    p_model_pooled: float
    p_domain_offset: float
    p_history: Optional[float]
    weights: dict  # w_market, w_model, w_domain, w_history
    uncertainty: UncertaintyBreakdown
    action: str  # trust_model | shrink_toward_market | skip
    decisions: list  # human-readable trace of every guard that fired


def _horizon_weight(hours: float) -> float:
    if hours >= 24 * 7:
        return 0.9
    if hours >= 24:
        return 0.5
    if hours >= 1:
        return 0.2
    return 0.05


def borrowed_strength_estimate(
    inputs: BorrowedStrengthInputs,
    tracker: Optional[ReliabilityTracker] = None,
) -> BorrowedStrengthResult:
    """The v4 composed estimator. Pure function; agent.py wires it
    into the lifecycle.
    """
    decisions: list = []

    # 1. Compose model ensemble: median in logit space, weighted by
    # per-(model, domain) trust multipliers if tracker is provided.
    model_probs = []
    for model_name, p_yes in inputs.p_models:
        if tracker is not None:
            mult = tracker.trust_multiplier(model_name, inputs.domain)
        else:
            mult = 1.0
        model_probs.append((model_name, clamp(p_yes), mult))

    if model_probs:
        z_weighted = sum(mult * logit(p) for _, p, mult in model_probs)
        w_total = sum(mult for _, _, mult in model_probs)
        p_model_pooled = sigmoid(z_weighted / max(1e-9, w_total))
    else:
        p_model_pooled = inputs.p_market

    # Disagreement across ensemble
    model_disagreement_val = 0.0
    if len(model_probs) >= 2:
        from statistics import pstdev
        model_disagreement_val = pstdev(p for _, p, _ in model_probs)

    # 2. Apply Kalshi longshot guard before any blending
    guarded_p_model = kalshi_longshot_guard(
        p_market=inputs.p_market,
        p_model=p_model_pooled,
        source_quality=inputs.source_quality,
        model_agreement=max(0.0, 1.0 - model_disagreement_val / 0.20),
    )
    if guarded_p_model != p_model_pooled:
        decisions.append(
            f"kalshi_longshot_guard capped p_model "
            f"{p_model_pooled:.3f} -> {guarded_p_model:.3f}"
        )

    # 3. Domain offset from reliability tracker
    if tracker is not None and inputs.p_models:
        # Use the best-trusted model's domain offset as the domain signal
        best_model = max(model_probs, key=lambda mp: mp[2])[0]
        domain_offset = tracker.pool.domain_offset(best_model, inputs.domain)
    else:
        domain_offset = 0.0

    # 4. Uncertainty aggregate
    unc = aggregate_uncertainty(
        model_probs=[p for _, p, _ in model_probs],
        retrieval_conflict=inputs.retrieval_conflict,
        yes_bid=inputs.yes_bid,
        yes_ask=inputs.yes_ask,
        hours_to_resolution=inputs.hours_to_resolution,
    )

    # 5. Compute weights
    horizon_w = _horizon_weight(inputs.hours_to_resolution)
    base_cred = credibility(
        source_quality=inputs.source_quality,
        model_agreement=max(0.0, 1.0 - model_disagreement_val / 0.20),
        horizon_weight=horizon_w,
    )
    # Domain reliability damps credibility on bad domains
    domain_reliability_damp = math.exp(-2.0 * max(0.0, domain_offset))
    # Average per-(model, domain) trust multiplier across the ensemble.
    # Clamped to [_, 1.0] so it acts only as a damp on cold/bad cells;
    # well-trusted cells get full credibility (no asymmetric boost).
    if tracker is not None and inputs.p_models:
        avg_mult = sum(
            tracker.trust_multiplier(name, inputs.domain)
            for name, _ in inputs.p_models
        ) / len(inputs.p_models)
        trust_damp = min(1.0, avg_mult)
    else:
        trust_damp = 1.0
    w_model = base_cred * domain_reliability_damp * trust_damp

    # History weight scales with n_history (more resolutions -> more borrow)
    if inputs.p_history is not None and inputs.n_history > 0:
        w_history = min(0.30, 0.05 + 0.005 * inputs.n_history)
    else:
        w_history = 0.0

    w_market = max(0.05, 1.0 - w_model - w_history)
    # Domain offset rolls into model weight (no separate w_domain), but
    # we surface it for telemetry.
    weights = {
        "w_market": round(w_market, 4),
        "w_model": round(w_model, 4),
        "w_history": round(w_history, 4),
        "w_domain_damp": round(domain_reliability_damp, 4),
        "w_trust_damp": round(trust_damp, 4),
    }

    # 6. Logit-space composition
    z_market = logit(inputs.p_market)
    z_model = logit(guarded_p_model)
    if inputs.p_history is not None and w_history > 0:
        z_history = logit(inputs.p_history)
    else:
        z_history = z_market  # fallback so 0 weight stays neutral
    z_blend = w_market * z_market + w_model * z_model + w_history * z_history
    p_blend = sigmoid(z_blend)

    # 7. Favorites no-shrink at the tail
    p_after_fav = favorites_no_shrink(inputs.p_market, p_blend)
    if p_after_fav != p_blend:
        decisions.append(
            f"favorites_no_shrink lifted p_final "
            f"{p_blend:.3f} -> {p_after_fav:.3f}"
        )

    p_final = clamp(p_after_fav)

    # 8. Action from uncertainty gate
    if unc.aggregate >= 0.60:
        action = "skip"
        decisions.append(
            f"uncertainty {unc.aggregate:.3f} >= 0.60 -> SKIP"
        )
    elif unc.aggregate >= 0.30:
        # Pull harder toward market
        p_final = clamp(
            sigmoid(0.6 * z_market + 0.4 * logit(p_final))
        )
        action = "shrink_toward_market"
        decisions.append(
            f"uncertainty {unc.aggregate:.3f} in [0.30, 0.60) -> "
            f"extra shrink toward market"
        )
    else:
        action = "trust_model"

    return BorrowedStrengthResult(
        p_final=p_final,
        p_market=inputs.p_market,
        p_model_pooled=guarded_p_model,
        p_domain_offset=domain_offset,
        p_history=inputs.p_history,
        weights=weights,
        uncertainty=unc,
        action=action,
        decisions=decisions,
    )
