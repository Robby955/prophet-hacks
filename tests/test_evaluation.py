"""Tests for evaluation/* — Brier, ECE, returns, no-leakage."""

from datetime import datetime, timedelta, timezone

import pytest

from evaluation.brier import (
    brier_score,
    brier_skill_score,
    mean_brier,
    murphy_decomposition,
    pnl_alpha_vs_market,
)
from evaluation.ece import (
    expected_calibration_error,
    maximum_calibration_error,
    reliability_diagram_data,
)
from evaluation.no_leakage_check import assert_no_leakage, check_no_leakage
from evaluation.returns import (
    PRICE_BUCKETS,
    Trade,
    per_trade_returns,
    pnl_by_price_bucket,
    sharpe,
    simulated_return,
    trade_payoff,
)


# -- Brier ----------------------------------------------------------------


def test_brier_perfect_prediction():
    assert brier_score(1.0, 1) == 0.0
    assert brier_score(0.0, 0) == 0.0


def test_brier_worst_prediction():
    assert brier_score(0.0, 1) == 1.0
    assert brier_score(1.0, 0) == 1.0


def test_brier_random_baseline():
    # Predicting 0.5 always -> Brier = 0.25
    probs = [0.5] * 10
    outcomes = [0, 1] * 5
    assert mean_brier(probs, outcomes) == 0.25


def test_brier_skill_score_perfect():
    assert brier_skill_score(0.0, 0.25) == 1.0


def test_brier_skill_score_baseline():
    assert brier_skill_score(0.25, 0.25) == 0.0


def test_brier_skill_score_worse_than_baseline():
    # Model brier 0.50, baseline 0.25 -> BSS = 1 - 2 = -1
    assert brier_skill_score(0.50, 0.25) == -1.0


def test_murphy_decomposition_identity():
    # Brier should equal reliability - resolution + uncertainty
    probs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
    outcomes = [0, 0, 0, 1, 0, 1, 1, 1, 1, 1]
    m = murphy_decomposition(probs, outcomes, n_bins=10)
    assert abs(m.brier - (m.reliability - m.resolution + m.uncertainty)) < 1e-9


def test_alpha_vs_market_positive_when_model_beats_market():
    # market predicts 0.5 on a YES outcome, model predicts 0.9
    # brier_market = 0.25, brier_model = 0.01, alpha = 0.24
    alpha = pnl_alpha_vs_market(p_final=0.9, p_market=0.5, outcome=1)
    assert abs(alpha - 0.24) < 1e-9


# -- ECE ------------------------------------------------------------------


def test_ece_perfect_calibration():
    # If we predict 0.5 on a 50/50 split, ECE should be 0
    probs = [0.5] * 10
    outcomes = [0, 1] * 5
    # All predictions in the bin containing 0.5 -> p_bar = 0.5, o_bar = 0.5
    # ECE = |0.5 - 0.5| = 0
    assert expected_calibration_error(probs, outcomes, n_bins=2) == 0.0


def test_ece_perfect_miscalibration():
    # Predict 0.9 always, outcome is always 0 -> ECE = 0.9
    probs = [0.9] * 10
    outcomes = [0] * 10
    ece = expected_calibration_error(probs, outcomes, n_bins=10)
    assert abs(ece - 0.9) < 1e-9


def test_mce_isolates_worst_bin():
    # 9 well-calibrated bins + 1 disastrous bin
    probs = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    outcomes = [0, 0, 0, 0, 0, 1, 1, 1, 1, 0]  # last bin: pred 0.95, outcome 0 -> gap 0.95
    mce = maximum_calibration_error(probs, outcomes, n_bins=10)
    assert mce > 0.9


def test_reliability_diagram_data_shape():
    probs = [0.1, 0.3, 0.5, 0.7, 0.9]
    outcomes = [0, 0, 1, 1, 1]
    data = reliability_diagram_data(probs, outcomes, n_bins=10)
    assert len(data) == 10
    assert all(0 <= b.p_mean <= 1 for b in data)


# -- Returns + price-bucket -----------------------------------------------


def _t(price, outcome, side="YES", notional=100.0, p_market=None) -> Trade:
    return Trade(
        market_id=f"m-{price}",
        side=side,
        notional=notional,
        fill_price=price,
        outcome=outcome,
        p_market_at_fill=p_market if p_market is not None else price,
        p_final=price,
    )


def test_trade_payoff_yes_winner():
    # Bought 200 shares at $0.50 each ($100 notional) -> wins $200 - $100 = $100
    t = _t(0.5, 1)
    assert trade_payoff(t) == 100.0


def test_trade_payoff_yes_loser():
    t = _t(0.5, 0)
    assert trade_payoff(t) == -100.0


def test_simulated_return_aggregates():
    trades = [_t(0.5, 1), _t(0.5, 0), _t(0.5, 1)]
    assert simulated_return(trades) == 100.0


def test_sharpe_finite():
    trades = [_t(0.5, 1), _t(0.5, 0), _t(0.5, 1), _t(0.5, 0)]
    s = sharpe(trades)
    assert isinstance(s, float)


def test_pnl_by_price_bucket_segregates_correctly():
    # One long-shot loser, one favorite winner
    trades = [_t(0.05, 0, p_market=0.05), _t(0.92, 1, p_market=0.92)]
    buckets = pnl_by_price_bucket(trades)
    assert len(buckets) == len(PRICE_BUCKETS)
    # Bucket [0.00, 0.10): one loser
    assert buckets[0].n_trades == 1
    assert buckets[0].total_pnl == -100.0
    # Bucket [0.90, 1.00]: one winner
    assert buckets[-1].n_trades == 1
    assert buckets[-1].total_pnl > 0


# -- No-leakage check -----------------------------------------------------


def test_no_leakage_clean_dataset_passes():
    dataset = [
        {
            "event_id": "e1",
            "forecast_time": "2026-05-01T12:00:00+00:00",
            "sources": [
                {"url": "u1", "published_at": "2026-04-15T08:00:00+00:00"},
                {"url": "u2", "published_at": "2026-05-01T11:00:00+00:00"},
            ],
        }
    ]
    assert check_no_leakage(dataset) == []
    assert_no_leakage(dataset)  # should not raise


def test_no_leakage_detects_post_forecast_source():
    dataset = [
        {
            "event_id": "e1",
            "forecast_time": "2026-05-01T12:00:00+00:00",
            "sources": [
                {"url": "future-source", "published_at": "2026-05-02T08:00:00+00:00"},
            ],
        }
    ]
    violations = check_no_leakage(dataset)
    assert len(violations) == 1
    assert "future-source" in str(violations[0])
    with pytest.raises(RuntimeError):
        assert_no_leakage(dataset)


def test_no_leakage_tolerates_missing_timestamps():
    dataset = [
        {
            "event_id": "e1",
            "forecast_time": "2026-05-01T12:00:00+00:00",
            "sources": [
                {"url": "no-pub-date"},
                {"url": "also-no-date", "published_at": None},
            ],
        }
    ]
    # Missing published_at -> we don't have evidence of leakage; pass.
    assert check_no_leakage(dataset) == []
