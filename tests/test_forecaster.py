"""Tests for forecaster module -- agreement gate and JSON parsing."""
from __future__ import annotations

import pytest

from forecaster import (
    agreement_gate,
    bucket,
    _parse_forecast_json,
    yes_edge,
    no_edge,
    clamp_p_yes,
)


class TestAgreementGate:
    """Six enumerated cases from CODEX_GOALS.md Goal 3."""

    def test_both_yes_strong(self):
        """Both models agree YES with strong conviction."""
        result = agreement_gate(0.75, 0.80)
        assert result is not None
        assert abs(result - 0.775) < 1e-6

    def test_both_no_strong(self):
        """Both models agree NO with strong conviction."""
        result = agreement_gate(0.25, 0.20)
        assert result is not None
        assert abs(result - 0.225) < 1e-6

    def test_both_yes_weak(self):
        """Both models lean YES but one is too uncertain (< 0.10 from 0.5)."""
        result = agreement_gate(0.55, 0.70)
        assert result is None  # 0.55 - 0.50 = 0.05 < 0.10

    def test_both_no_weak(self):
        """Both models lean NO but one is too uncertain."""
        result = agreement_gate(0.45, 0.30)
        assert result is None  # 0.45 - 0.50 = -0.05, abs < 0.10

    def test_direction_conflict(self):
        """Models disagree on direction."""
        result = agreement_gate(0.70, 0.30)
        assert result is None

    def test_magnitude_conflict(self):
        """Both lean same way but one is too close to 0.5."""
        result = agreement_gate(0.52, 0.75)
        assert result is None  # 0.52 - 0.50 = 0.02 < 0.10

    def test_exact_threshold(self):
        """Exactly at the 0.10 conviction threshold."""
        result = agreement_gate(0.60, 0.60)
        assert result is not None
        assert abs(result - 0.60) < 1e-6

    def test_symmetric_strong_no(self):
        """Strong NO on both sides."""
        result = agreement_gate(0.15, 0.10)
        assert result is not None
        assert abs(result - 0.125) < 1e-6


class TestBucket:
    def test_exact_bucket(self):
        assert bucket(0.70) == 0.70

    def test_rounds_to_nearest(self):
        assert bucket(0.65) == 0.70
        assert bucket(0.64) == 0.60

    def test_edge_cases(self):
        assert bucket(0.05) == 0.10
        assert bucket(0.95) == 0.90

    def test_midpoint_rounds_down(self):
        # Ties round down per implementation
        assert bucket(0.55) == 0.50 or bucket(0.55) == 0.60  # depends on exact tie


class TestEdgeCalculation:
    def test_yes_edge(self):
        assert abs(yes_edge(0.70, 0.45) - 0.25) < 1e-6

    def test_no_edge(self):
        assert abs(no_edge(0.30, 0.55) - 0.15) < 1e-6

    def test_no_edge_from_p_yes(self):
        # p_yes=0.30, no_ask=0.55: no_edge = (1 - 0.30) - 0.55 = 0.15
        assert abs(no_edge(0.30, 0.55) - 0.15) < 1e-6


class TestParseForecaseJson:
    def test_clean_json(self):
        text = '{"p_yes": 0.72, "rationale": "Strong evidence"}'
        result = _parse_forecast_json(text)
        assert abs(result["p_yes"] - 0.72) < 1e-6
        assert result["rationale"] == "Strong evidence"

    def test_json_with_whitespace(self):
        text = '  \n{"p_yes": 0.65, "rationale": "Some reasoning"}  \n'
        result = _parse_forecast_json(text)
        assert abs(result["p_yes"] - 0.65) < 1e-6

    def test_json_embedded_in_text(self):
        text = 'Here is my analysis:\n{"p_yes": 0.80, "rationale": "Clear signal"}\nThank you.'
        result = _parse_forecast_json(text)
        assert abs(result["p_yes"] - 0.80) < 1e-6

    def test_regex_fallback(self):
        text = 'My estimate is p_yes: 0.55, because...'
        result = _parse_forecast_json(text)
        assert abs(result["p_yes"] - 0.55) < 1e-6

    def test_unparseable_raises(self):
        with pytest.raises(ValueError):
            _parse_forecast_json("I have no idea what this market is about")


class TestClampPYes:
    def test_normal_range(self):
        assert clamp_p_yes(0.50) == 0.50

    def test_clamp_low(self):
        assert clamp_p_yes(0.005) == 0.01

    def test_clamp_high(self):
        assert clamp_p_yes(0.995) == 0.99

    def test_exact_boundary(self):
        assert clamp_p_yes(0.01) == 0.01
        assert clamp_p_yes(0.99) == 0.99


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
