"""Tests for risk module -- hard caps and invariants."""
from __future__ import annotations

import pytest
from risk import (
    RiskViolation,
    assert_no_conflicting_position,
    assert_under_notional_cap,
    assert_under_position_count,
    assert_under_trades_per_tick,
    position_size_for_notional,
    clamp_p_yes,
    EDGE_THRESHOLD,
    MAX_TRADES_PER_TICK,
    MAX_NOTIONAL_PER_NEW_POSITION,
    MAX_NOTIONAL_PER_MARKET,
    MAX_OPEN_POSITIONS,
)


class FakePosition:
    def __init__(self, market_id: str, side: str, shares: float = 10, avg_entry_price: float = 0.5):
        self.market_id = market_id
        self.side = side
        self.shares = shares
        self.avg_entry_price = avg_entry_price


class TestConflictingPosition:
    def test_no_positions(self):
        assert_no_conflicting_position("m1", "YES", [])

    def test_same_side_ok(self):
        pos = [FakePosition("m1", "YES")]
        assert_no_conflicting_position("m1", "YES", pos)

    def test_different_market_ok(self):
        pos = [FakePosition("m2", "NO")]
        assert_no_conflicting_position("m1", "YES", pos)

    def test_opposite_side_blocks(self):
        pos = [FakePosition("m1", "NO")]
        with pytest.raises(RiskViolation):
            assert_no_conflicting_position("m1", "YES", pos)

    def test_case_insensitive(self):
        pos = [FakePosition("m1", "no")]
        with pytest.raises(RiskViolation):
            assert_no_conflicting_position("m1", "yes", pos)


class TestNotionalCap:
    def test_under_cap(self):
        assert_under_notional_cap(50.0)

    def test_at_cap(self):
        assert_under_notional_cap(MAX_NOTIONAL_PER_NEW_POSITION)

    def test_over_cap(self):
        with pytest.raises(RiskViolation):
            assert_under_notional_cap(MAX_NOTIONAL_PER_NEW_POSITION + 0.01)

    def test_market_cap(self):
        assert_under_notional_cap(50.0, current_market_notional=900.0)

    def test_market_cap_exceeded(self):
        with pytest.raises(RiskViolation):
            assert_under_notional_cap(200.0, current_market_notional=900.0)


class TestPositionCount:
    def test_under_cap(self):
        assert_under_position_count(10)

    def test_at_cap(self):
        with pytest.raises(RiskViolation):
            assert_under_position_count(MAX_OPEN_POSITIONS)


class TestTradesPerTick:
    def test_under_cap(self):
        assert_under_trades_per_tick(0)

    def test_at_cap(self):
        with pytest.raises(RiskViolation):
            assert_under_trades_per_tick(MAX_TRADES_PER_TICK)


class TestPositionSize:
    def test_normal(self):
        size = position_size_for_notional(100.0, 0.5)
        assert size == 200

    def test_rounds_down(self):
        size = position_size_for_notional(100.0, 0.33)
        assert size == 303  # floor(100/0.33) = floor(303.03) = 303

    def test_zero_price(self):
        assert position_size_for_notional(100.0, 0.0) == 0

    def test_negative_price(self):
        assert position_size_for_notional(100.0, -0.1) == 0


class TestConstants:
    """Verify constants match the documented values."""

    def test_edge_threshold(self):
        assert EDGE_THRESHOLD == 0.08

    def test_max_trades(self):
        assert MAX_TRADES_PER_TICK == 3

    def test_max_notional(self):
        assert MAX_NOTIONAL_PER_NEW_POSITION == 100.0

    def test_max_market_notional(self):
        assert MAX_NOTIONAL_PER_MARKET == 1000.0

    def test_max_positions(self):
        assert MAX_OPEN_POSITIONS == 30
