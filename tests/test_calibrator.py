"""Calibrator unit tests."""
import math

import calibrator


def test_logit_sigmoid_roundtrip():
    for p in (0.05, 0.2, 0.5, 0.73, 0.95):
        assert math.isclose(calibrator.sigmoid(calibrator.logit(p)), p, abs_tol=1e-9)


def test_logit_clamps_extremes():
    # Inputs at 0 or 1 must not produce +/- inf
    assert math.isfinite(calibrator.logit(0.0))
    assert math.isfinite(calibrator.logit(1.0))
    # sigmoid is numerically stable at large |x| (no overflow, in [0, 1])
    assert 0.0 <= calibrator.sigmoid(50.0) <= 1.0
    assert 0.0 <= calibrator.sigmoid(-50.0) <= 1.0
    assert math.isfinite(calibrator.sigmoid(1000.0))
    assert math.isfinite(calibrator.sigmoid(-1000.0))


def test_blend_forecast_zero_evidence_stays_near_market():
    p_market = 0.30
    p_model = 0.80
    p_final = calibrator.blend_forecast(p_market, p_model, evidence_quality=0.0)
    # With zero evidence, weight on model is small; final should be much closer to market.
    assert abs(p_final - p_market) < abs(p_final - p_model)


def test_blend_forecast_high_evidence_moves_toward_model():
    p_market = 0.30
    p_model = 0.80
    low = calibrator.blend_forecast(p_market, p_model, evidence_quality=0.0)
    high = calibrator.blend_forecast(p_market, p_model, evidence_quality=1.0)
    # More evidence -> further from market in the direction of the model.
    assert (high - p_market) > (low - p_market)


def test_blend_forecast_bounds():
    for p_market in (0.05, 0.5, 0.95):
        for p_model in (0.05, 0.5, 0.95):
            for eq in (0.0, 0.5, 1.0):
                p = calibrator.blend_forecast(p_market, p_model, eq)
                assert 0.0 < p < 1.0


def test_shrink_toward_anchor():
    assert math.isclose(calibrator.shrink(0.9, tau=0.0, anchor=0.5), 0.5)
    assert math.isclose(calibrator.shrink(0.9, tau=1.0, anchor=0.5), 0.9)
    assert math.isclose(calibrator.shrink(0.9, tau=0.5, anchor=0.5), 0.7)


def test_shrink_default_anchor_is_half():
    # tau=0.5, anchor None defaults to 0.5
    assert math.isclose(calibrator.shrink(0.8, tau=0.5), 0.65)


def test_confidence_bucket():
    assert calibrator.confidence_bucket(0.5) == "low"
    assert calibrator.confidence_bucket(0.55) == "low"
    assert calibrator.confidence_bucket(0.65) == "medium"
    assert calibrator.confidence_bucket(0.85) == "high"
    # |0.20 - 0.5| = 0.30 -> beyond the high threshold (0.25)
    assert calibrator.confidence_bucket(0.20) == "high"
    assert calibrator.confidence_bucket(0.05) == "high"
