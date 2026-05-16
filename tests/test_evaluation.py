from datetime import datetime, timezone

import pytest

from evaluation.brier import (
    brier_score,
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
    Trade,
    per_trade_returns,
    pnl_by_price_bucket,
    simulated_return,
    trade_payoff,
)


def test_brier_score_validates_probability_and_outcome() -> None:
    assert brier_score(0.25, 1) == pytest.approx(0.5625)

    with pytest.raises(ValueError, match=r"probability.*\[0, 1\]"):
        brier_score(-0.1, 1)
    with pytest.raises(ValueError, match="outcome"):
        brier_score(0.5, 2)


def test_brier_series_and_decomposition_validate_inputs() -> None:
    with pytest.raises(ValueError, match="same length"):
        mean_brier([0.5], [1, 0])
    with pytest.raises(ValueError, match="n_bins"):
        murphy_decomposition([0.5], [1], n_bins=0)
    with pytest.raises(ValueError, match=r"probs\[0\]"):
        murphy_decomposition([1.2], [1])

    dec = murphy_decomposition([0.2, 0.8], [0, 1], n_bins=2)
    assert dec.brier == pytest.approx(mean_brier([0.2, 0.8], [0, 1]))


def test_ece_validates_inputs_consistently() -> None:
    with pytest.raises(ValueError, match=r"probs\[0\]"):
        expected_calibration_error([-0.1], [0])
    with pytest.raises(ValueError, match="same length"):
        maximum_calibration_error([0.5], [1, 0])
    with pytest.raises(ValueError, match="n_bins"):
        reliability_diagram_data([0.5], [1], n_bins=0)

    bins = reliability_diagram_data([], [], n_bins=3)
    assert [b.center for b in bins] == pytest.approx([1 / 6, 0.5, 5 / 6])
    assert all(b.count == 0 for b in bins)


def test_pnl_alpha_vs_market_is_positive_when_final_beats_market() -> None:
    assert pnl_alpha_vs_market(p_final=0.8, p_market=0.5, outcome=1) > 0


def test_trade_payoff_yes_and_no_contracts() -> None:
    yes_win = Trade("m1", "YES", 100.0, 0.25, 1, 0.25, 0.8)
    yes_loss = Trade("m2", "YES", 100.0, 0.25, 0, 0.25, 0.2)
    no_win = Trade("m3", "NO", 100.0, 0.75, 0, 0.75, 0.2)
    no_loss = Trade("m4", "NO", 100.0, 0.75, 1, 0.75, 0.8)

    assert trade_payoff(yes_win) == pytest.approx(300.0)
    assert trade_payoff(yes_loss) == pytest.approx(-100.0)
    assert trade_payoff(no_win) == pytest.approx(300.0)
    assert trade_payoff(no_loss) == pytest.approx(-100.0)
    assert simulated_return([yes_win, yes_loss, no_win, no_loss]) == pytest.approx(
        400.0
    )
    assert per_trade_returns([yes_win, yes_loss]) == pytest.approx([3.0, -1.0])


def test_trade_payoff_rejects_impossible_prices_and_bad_fields() -> None:
    with pytest.raises(ValueError, match="YES contract price"):
        trade_payoff(Trade("m", "YES", 100.0, 0.0, 1, 0.0, 0.8))
    with pytest.raises(ValueError, match="NO contract price"):
        trade_payoff(Trade("m", "NO", 100.0, 1.0, 0, 1.0, 0.2))
    with pytest.raises(ValueError, match="side"):
        trade_payoff(Trade("m", "MAYBE", 100.0, 0.5, 1, 0.5, 0.8))
    with pytest.raises(ValueError, match="notional"):
        trade_payoff(Trade("m", "YES", 0.0, 0.5, 1, 0.5, 0.8))
    with pytest.raises(ValueError, match="outcome"):
        trade_payoff(Trade("m", "YES", 100.0, 0.5, 2, 0.5, 0.8))


def test_pnl_by_price_bucket_rejects_invalid_market_probability() -> None:
    with pytest.raises(ValueError, match="p_market_at_fill"):
        pnl_by_price_bucket([Trade("m", "YES", 100.0, 0.5, 1, -0.01, 0.8)])


def test_no_leakage_check_reports_future_sources() -> None:
    dataset = [
        {
            "event_id": "clean",
            "forecast_time": "2026-05-16T12:00:00Z",
            "sources": [
                {
                    "url": "https://example.com/before",
                    "published_at": "2026-05-16T11:59:00Z",
                }
            ],
        },
        {
            "event_id": "leaky",
            "forecast_time": datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc),
            "sources": [
                {
                    "url": "https://example.com/after",
                    "published_at": "2026-05-16T12:01:00Z",
                }
            ],
        },
    ]

    violations = check_no_leakage(dataset)
    assert len(violations) == 1
    assert violations[0].event_id == "leaky"
    with pytest.raises(RuntimeError, match="Leakage detected"):
        assert_no_leakage(dataset)
