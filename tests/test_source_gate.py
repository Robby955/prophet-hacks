"""Tests for forecasting.source_gate — retrieval decisions."""

from forecasting.source_gate import (
    HIGH_SIGNAL_DOMAINS,
    LOW_SIGNAL_DOMAINS,
    retrieval_cost_estimate,
    should_retrieve,
)


def test_no_budget_skips():
    d = should_retrieve(
        domain="sports", hours_to_resolution=24.0,
        market_implied_p_yes=0.5, cost_budget_remaining_usd=0.0,
    )
    assert not d.should_retrieve
    assert "no_budget" in d.reasons


def test_near_resolution_skips():
    d = should_retrieve(
        domain="sports", hours_to_resolution=0.5,
        market_implied_p_yes=0.5, cost_budget_remaining_usd=100.0,
    )
    assert not d.should_retrieve
    assert "near_resolution" in d.reasons


def test_high_signal_domain_retrieves():
    d = should_retrieve(
        domain="finance", hours_to_resolution=48.0,
        market_implied_p_yes=0.5, cost_budget_remaining_usd=100.0,
    )
    assert d.should_retrieve
    assert "high_signal_domain" in d.reasons
    assert d.source_cap >= 2


def test_low_signal_domain_skips_when_no_disagreement():
    d = should_retrieve(
        domain="geopolitics", hours_to_resolution=48.0,
        market_implied_p_yes=0.5, model_disagreement=0.05,
        cost_budget_remaining_usd=100.0,
    )
    assert not d.should_retrieve


def test_low_signal_domain_retrieves_on_meaningful_disagreement():
    d = should_retrieve(
        domain="geopolitics", hours_to_resolution=48.0,
        market_implied_p_yes=0.5, model_disagreement=0.20,
        cost_budget_remaining_usd=100.0,
    )
    # Low-signal-domain branch fires first and skips on no-disagreement;
    # with disagreement above 0.10 it falls through to the meaningful-disagreement branch
    assert d.should_retrieve
    assert "meaningful_disagreement" in d.reasons


def test_long_horizon_high_signal_gets_more_sources():
    d_long = should_retrieve(
        domain="elections", hours_to_resolution=24 * 14,
        market_implied_p_yes=0.5, cost_budget_remaining_usd=100.0,
    )
    d_short = should_retrieve(
        domain="elections", hours_to_resolution=4.0,
        market_implied_p_yes=0.5, cost_budget_remaining_usd=100.0,
    )
    assert d_long.source_cap >= d_short.source_cap


def test_retrieval_cost_estimate_zero_when_no_sources():
    assert retrieval_cost_estimate(0, 24.0) == 0.0


def test_retrieval_cost_estimate_grows_with_cap():
    a = retrieval_cost_estimate(2, 24.0)
    b = retrieval_cost_estimate(4, 24.0)
    assert b > a


def test_retrieval_cost_estimate_grows_with_long_horizon():
    short = retrieval_cost_estimate(3, 24.0)
    long_ = retrieval_cost_estimate(3, 24 * 14)
    assert long_ > short


def test_domain_classification_disjoint():
    assert not (HIGH_SIGNAL_DOMAINS & LOW_SIGNAL_DOMAINS)
