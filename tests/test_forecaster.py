"""Tests for forecaster module.

Covers behaviors preserved across the v2 rebase:
- bucket / edge / clamp primitives
- Gemini's three-level JSON parsing (now living in decomposition._extract_json)
- conviction-floor skip gate (now in stage_calibrator, _build_skip_reason)
"""
from __future__ import annotations

import pytest

import forecaster
from forecaster import (
    bucket,
    yes_edge,
    no_edge,
    stage_calibrator,
    _build_skip_reason,
)
from risk import clamp_p_yes
from decomposition import _extract_json


class TestBucket:
    def test_exact_bucket(self):
        assert bucket(0.70) == 0.70

    def test_rounds_to_nearest(self):
        assert bucket(0.65) == 0.70
        assert bucket(0.64) == 0.60

    def test_edge_cases(self):
        assert bucket(0.05) == 0.10
        assert bucket(0.95) == 0.90


class TestEdgeCalculation:
    def test_yes_edge(self):
        assert abs(yes_edge(0.70, 0.45) - 0.25) < 1e-6

    def test_no_edge(self):
        assert abs(no_edge(0.30, 0.55) - 0.15) < 1e-6


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


class TestDecompositionJsonParser:
    """Three-level extraction (direct -> anchored -> greedy). Preserved
    from Gemini's parser to survive noisy model output on Saturday."""

    def test_clean_json(self):
        text = (
            '{"raw_p_yes_before_market": 0.72, "base_rate": 0.5, '
            '"source_quality": 0.6, "should_shrink": false, '
            '"time_to_resolution_risk": "low"}'
        )
        result = _extract_json(text)
        assert abs(result["raw_p_yes_before_market"] - 0.72) < 1e-6

    def test_json_with_whitespace(self):
        text = (
            '  \n{"raw_p_yes_before_market": 0.65, "base_rate": 0.5, '
            '"source_quality": 0.5, "should_shrink": true, '
            '"time_to_resolution_risk": "medium"}  \n'
        )
        result = _extract_json(text)
        assert abs(result["raw_p_yes_before_market"] - 0.65) < 1e-6

    def test_json_embedded_with_prose(self):
        text = (
            "Here is my analysis:\n"
            '{"raw_p_yes_before_market": 0.80, "base_rate": 0.4, '
            '"source_quality": 0.8, "should_shrink": false, '
            '"time_to_resolution_risk": "low"}\nThank you.'
        )
        result = _extract_json(text)
        assert abs(result["raw_p_yes_before_market"] - 0.80) < 1e-6

    def test_pure_garbage_returns_none(self):
        assert _extract_json("I have no idea what this market is about") is None

    def test_empty_string(self):
        assert _extract_json("") is None


class TestMeaningfulConvictionFloor:
    """v2-equivalent of Gemini's agreement-gate. The conviction floor
    travels with stage_calibrator (sets `meaningful_conviction`) and is
    surfaced as a skip reason in _build_skip_reason."""

    def _calibrate(self, p_model_raw, market_p):
        candidate = {
            "p_market": market_p,
            "p_model_raw": p_model_raw,
            "evidence_quality": 0.5,
        }
        return stage_calibrator(candidate)

    def test_strong_yes_passes_floor(self):
        # Strong YES model + already-leaning-YES market clears the floor
        # even after the 0.75 tau-shrink toward market.
        c = self._calibrate(0.95, 0.70)
        assert c["meaningful_conviction"] is True
        assert c["p_final"] - 0.5 >= forecaster.MEANINGFUL_CONVICTION_FLOOR

    def test_strong_no_passes_floor(self):
        c = self._calibrate(0.05, 0.30)
        assert c["meaningful_conviction"] is True
        assert 0.5 - c["p_final"] >= forecaster.MEANINGFUL_CONVICTION_FLOOR

    def test_low_conviction_blocks(self):
        c = self._calibrate(0.52, 0.50)
        assert c["meaningful_conviction"] is False

    def test_skip_reason_surfaces_floor(self):
        candidate = {
            "p_final": 0.52,
            "meaningful_conviction": False,
            "edge_passes_gate": True,
            "disagreement_high": False,
            "yes_edge": 0.10,
            "no_edge": -0.10,
        }
        reason = _build_skip_reason(candidate)
        assert reason is not None
        assert "conviction floor" in reason

    def test_floor_is_configurable(self):
        assert forecaster.MEANINGFUL_CONVICTION_FLOOR >= 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
