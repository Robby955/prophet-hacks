"""Tests for forecasting.bidirectional — combine, consistency, gate."""

from forecasting.bidirectional import (
    bidirectional_consistency,
    combine_bidirectional,
    should_bidirectional,
)


def test_combine_consistent_inputs():
    # p_yes = 0.7, p_no = 0.3 -> 0.5 * (0.7 + 0.7) = 0.7
    assert abs(combine_bidirectional(0.7, 0.3) - 0.7) < 1e-9


def test_combine_inconsistent_averages():
    # p_yes = 0.7, p_no = 0.4 -> 0.5 * (0.7 + 0.6) = 0.65
    assert abs(combine_bidirectional(0.7, 0.4) - 0.65) < 1e-9


def test_combine_clamps_output():
    p = combine_bidirectional(0.99, 0.01)
    assert 0.01 <= p <= 0.99


def test_consistency_perfect():
    assert bidirectional_consistency(0.7, 0.3) == 1.0


def test_consistency_drifts_to_zero():
    # drift = |1.5 - 1.0| = 0.5 -> > 0.4 threshold -> 0
    assert bidirectional_consistency(0.9, 0.6) == 0.0


def test_should_bidirectional_near_threshold():
    assert should_bidirectional(edge=0.10, edge_threshold=0.08) is True
    assert should_bidirectional(edge=0.30, edge_threshold=0.08) is False


def test_should_bidirectional_high_notional():
    assert (
        should_bidirectional(edge=0.30, edge_threshold=0.08, market_notional=1000.0)
        is True
    )


def test_should_bidirectional_sources_conflict():
    assert (
        should_bidirectional(
            edge=0.30, edge_threshold=0.08, sources_conflict=0.5
        )
        is True
    )
