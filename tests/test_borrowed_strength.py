"""Tests for the v4 SAE-inspired composed estimator + supporting layers."""

import math
from pathlib import Path

import pytest

from forecasting.borrowed_strength import (
    BorrowedStrengthInputs,
    borrowed_strength_estimate,
)
from forecasting.domain_pools import DomainPool
from forecasting.reliability_tracking import ReliabilityTracker, default_tracker
from forecasting.uncertainty import (
    aggregate_uncertainty,
    horizon_proximity,
    model_disagreement,
    normalize_spread,
    uncertainty_action,
)


# -- DomainPool -----------------------------------------------------------


def test_domain_pool_thin_cell_shrinks_to_marginal():
    pool = DomainPool(lambda_prior=10.0)
    # Push the model's marginal up via two domains
    for _ in range(20):
        pool.update("gpt55", "sports", 0.20)
    for _ in range(20):
        pool.update("gpt55", "finance", 0.20)
    # A brand-new domain has n=0 -> should fall back to the marginal
    pooled = pool.pooled_brier("gpt55", "crypto")
    marginal = pool.marginals["gpt55"].mean_brier
    assert abs(pooled - marginal) < 1e-9


def test_domain_pool_thick_cell_uses_own_data():
    pool = DomainPool(lambda_prior=10.0)
    # 100 sports outcomes, all good
    for _ in range(100):
        pool.update("gpt55", "sports", 0.10)
    # Marginal pulled down to 0.10 too; pooled should match
    pooled = pool.pooled_brier("gpt55", "sports")
    assert abs(pooled - 0.10) < 1e-9


def test_domain_pool_offset_zero_when_cell_matches_marginal():
    pool = DomainPool()
    for _ in range(10):
        pool.update("gpt55", "sports", 0.20)
    offset = pool.domain_offset("gpt55", "sports")
    assert abs(offset) < 1e-9


def test_domain_pool_offset_positive_when_domain_worse():
    pool = DomainPool(lambda_prior=1.0)  # low lambda so cell uses its own data fast
    for _ in range(50):
        pool.update("gpt55", "geopolitics", 0.40)
    for _ in range(50):
        pool.update("gpt55", "sports", 0.10)
    offset = pool.domain_offset("gpt55", "geopolitics")
    assert offset > 0


def test_domain_pool_unknown_domain_routes_to_other():
    pool = DomainPool()
    pool.update("gpt55", "made-up-domain", 0.15)
    # Should have been routed to "other"
    assert ("gpt55", "other") in pool.cells
    assert ("gpt55", "made-up-domain") not in pool.cells


# -- ReliabilityTracker ---------------------------------------------------


def test_trust_multiplier_clamps_within_bounds():
    tracker = default_tracker()
    # No data -> pooled brier defaults to 0.25 (random baseline)
    m = tracker.trust_multiplier("gpt55", "sports")
    assert tracker.multiplier_min <= m <= tracker.multiplier_max


def test_trust_multiplier_rises_with_good_track_record():
    tracker = default_tracker()
    for _ in range(20):
        tracker.record(model="gpt55", domain="sports", brier=0.10)
    m_good = tracker.trust_multiplier("gpt55", "sports")
    for _ in range(20):
        tracker.record(model="gpt55", domain="geopolitics", brier=0.35)
    m_bad = tracker.trust_multiplier("gpt55", "geopolitics")
    assert m_good > m_bad


def test_state_persistence_round_trip(tmp_path: Path):
    state = tmp_path / "reliability.jsonl"
    tracker = default_tracker(state_path=state)
    tracker.record(model="opus", domain="elections", brier=0.12)
    # Re-instantiate and load
    tracker2 = default_tracker(state_path=state)
    tracker2.load()
    assert tracker2.pool.cells.get(("opus", "elections")) is not None


# -- Uncertainty ----------------------------------------------------------


def test_model_disagreement_empty_or_singleton_is_zero():
    assert model_disagreement([]) == 0.0
    assert model_disagreement([0.5]) == 0.0


def test_model_disagreement_increases_with_spread():
    a = model_disagreement([0.45, 0.55])
    b = model_disagreement([0.20, 0.80])
    assert b > a


def test_normalize_spread_clamps():
    assert normalize_spread(0.5, 0.5) == 0.0
    assert normalize_spread(0.0, 0.50) == 1.0


def test_horizon_proximity_extremes():
    assert horizon_proximity(0.5) == 1.0  # < 1 hour
    assert horizon_proximity(24 * 14) == 0.0  # 2 weeks


def test_aggregate_uncertainty_returns_breakdown():
    breakdown = aggregate_uncertainty(
        model_probs=[0.4, 0.6],
        retrieval_conflict=0.2,
        yes_bid=0.45,
        yes_ask=0.55,
        hours_to_resolution=48.0,
    )
    assert 0.0 <= breakdown.aggregate <= 1.0
    assert breakdown.model_disagreement > 0


def test_uncertainty_action_tiers():
    assert uncertainty_action(0.10) == "trust_model"
    assert uncertainty_action(0.45) == "shrink_toward_market"
    assert uncertainty_action(0.80) == "skip"


# -- Borrowed strength composer ------------------------------------------


def _inputs(**kwargs) -> BorrowedStrengthInputs:
    base = dict(
        p_market=0.50,
        p_models=[("gpt55", 0.60), ("opus", 0.55)],
        domain="sports",
        source_quality=0.7,
        retrieval_conflict=0.0,
        hours_to_resolution=48.0,
        yes_bid=0.49,
        yes_ask=0.51,
        p_history=None,
        n_history=0,
    )
    base.update(kwargs)
    return BorrowedStrengthInputs(**base)


def test_borrowed_strength_returns_valid_probability():
    result = borrowed_strength_estimate(_inputs())
    assert 0.01 <= result.p_final <= 0.99


def test_borrowed_strength_high_uncertainty_skips():
    # Large model disagreement + wide spread + sources conflict -> skip
    result = borrowed_strength_estimate(
        _inputs(
            p_models=[("gpt55", 0.20), ("opus", 0.80)],
            retrieval_conflict=0.9,
            yes_bid=0.30,
            yes_ask=0.70,  # huge spread
            hours_to_resolution=0.5,
        )
    )
    assert result.action == "skip"


def test_borrowed_strength_low_uncertainty_trusts_model():
    result = borrowed_strength_estimate(
        _inputs(
            p_models=[("gpt55", 0.62), ("opus", 0.60)],  # tight
            source_quality=0.9,
            retrieval_conflict=0.0,
            yes_bid=0.49,
            yes_ask=0.51,
            hours_to_resolution=48.0,
        )
    )
    assert result.action == "trust_model"


def test_borrowed_strength_longshot_guard_fires_in_low_market():
    result = borrowed_strength_estimate(
        _inputs(
            p_market=0.05,
            p_models=[("gpt55", 0.40), ("opus", 0.35)],
            source_quality=0.3,
            retrieval_conflict=0.2,
        )
    )
    # Model wanted ~0.37 average but Kalshi guard caps the upward move
    assert result.p_model_pooled <= 0.05 + 0.05 + 1e-9
    assert any("kalshi_longshot_guard" in d for d in result.decisions)


def test_borrowed_strength_favorite_no_shrink_fires_at_top():
    result = borrowed_strength_estimate(
        _inputs(
            p_market=0.92,
            p_models=[("gpt55", 0.60), ("opus", 0.55)],
            source_quality=0.4,
            hours_to_resolution=24.0,
        )
    )
    # After blend, favorites_no_shrink should pull us back up.
    assert any("favorites_no_shrink" in d for d in result.decisions)
    assert result.p_final > 0.65


def test_borrowed_strength_history_weight_grows_with_n():
    no_hist = borrowed_strength_estimate(_inputs())
    with_hist = borrowed_strength_estimate(
        _inputs(p_history=0.60, n_history=50)
    )
    assert with_hist.weights["w_history"] > no_hist.weights["w_history"]


def test_borrowed_strength_reliability_damps_bad_domain():
    tracker = default_tracker()
    # Train tracker to dislike geopolitics for gpt55
    for _ in range(50):
        tracker.record(model="gpt55", domain="geopolitics", brier=0.45)
    for _ in range(50):
        tracker.record(model="gpt55", domain="sports", brier=0.10)

    sports_result = borrowed_strength_estimate(
        _inputs(domain="sports"), tracker=tracker
    )
    geo_result = borrowed_strength_estimate(
        _inputs(domain="geopolitics"), tracker=tracker
    )
    # Sports should give the model more weight than geopolitics
    assert sports_result.weights["w_model"] > geo_result.weights["w_model"]
