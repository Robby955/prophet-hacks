"""Tests for forecasting.market_blend — Kalshi guards + credibility blend."""

from forecasting.market_blend import (
    blend_market_and_model,
    clamp,
    credibility,
    favorites_no_shrink,
    full_blend,
    kalshi_longshot_guard,
    logit,
    longshot_proximity,
    sigmoid,
)


# -- Numeric helpers ------------------------------------------------------


def test_clamp_bounds():
    assert clamp(0.0) == 0.01
    assert clamp(1.0) == 0.99
    assert clamp(0.5) == 0.5


def test_logit_sigmoid_roundtrip():
    for p in [0.05, 0.2, 0.5, 0.8, 0.95]:
        assert abs(sigmoid(logit(p)) - p) < 1e-9


# -- Kalshi longshot guard ------------------------------------------------


def test_longshot_guard_passthrough_above_threshold():
    # p_market >= 0.10 -> guard does nothing
    assert kalshi_longshot_guard(0.15, 0.30, 0.5, 0.5) == 0.30
    assert kalshi_longshot_guard(0.50, 0.80, 0.5, 0.5) == 0.80


def test_longshot_guard_caps_low_evidence_upward_move():
    # p_market < 0.10 and weak evidence and large upward move -> cap at +0.05
    capped = kalshi_longshot_guard(
        p_market=0.05, p_model=0.40, source_quality=0.3, model_agreement=0.4
    )
    assert capped == 0.10  # 0.05 + 0.05


def test_longshot_guard_allows_strong_evidence():
    # Strong evidence -> let the model through
    p = kalshi_longshot_guard(
        p_market=0.05, p_model=0.40, source_quality=0.9, model_agreement=0.9
    )
    assert p == 0.40


def test_longshot_guard_allows_small_upward_move():
    # Upward move <= 0.05 -> let the model through
    p = kalshi_longshot_guard(
        p_market=0.04, p_model=0.08, source_quality=0.2, model_agreement=0.2
    )
    assert p == 0.08


# -- Favorites no-shrink --------------------------------------------------


def test_favorites_no_shrink_passthrough_when_market_low():
    assert favorites_no_shrink(0.50, 0.40) == 0.40
    assert favorites_no_shrink(0.84, 0.60) == 0.60


def test_favorites_no_shrink_blends_when_model_lower():
    # p_market = 0.92, p_model = 0.60 -> 0.7*0.92 + 0.3*0.60 = 0.644 + 0.180 = 0.824
    p = favorites_no_shrink(0.92, 0.60)
    assert abs(p - (0.7 * 0.92 + 0.3 * 0.60)) < 1e-9


def test_favorites_no_shrink_passthrough_when_model_higher():
    assert favorites_no_shrink(0.90, 0.95) == 0.95


# -- Longshot proximity ---------------------------------------------------


def test_longshot_proximity_zero_above_threshold():
    assert longshot_proximity(0.10) == 0.0
    assert longshot_proximity(0.50) == 0.0


def test_longshot_proximity_ramps_to_one():
    assert abs(longshot_proximity(0.05) - 0.5) < 1e-9
    assert longshot_proximity(0.0) == 1.0


# -- Credibility ----------------------------------------------------------


def test_credibility_bounds():
    # All zero inputs -> floor (0.10), clamped to 0.05 floor? Actually 0.10
    # is already above the 0.05 lower bound.
    c = credibility(0.0, 0.0, 0.0)
    assert 0.05 <= c <= 0.75
    assert abs(c - 0.10) < 1e-9


def test_credibility_max():
    c = credibility(1.0, 1.0, 1.0)
    # 0.10 + 0.45 + 0.25 + 0.20 = 1.0 -> clamped to 0.75
    assert c == 0.75


def test_credibility_monotone_in_source_quality():
    c_lo = credibility(0.2, 0.5, 0.5)
    c_hi = credibility(0.8, 0.5, 0.5)
    assert c_hi > c_lo


# -- Blend ----------------------------------------------------------------


def test_blend_pulls_toward_market_when_credibility_low():
    # source_quality 0, agreement 0, horizon 0 -> credibility = 0.10
    # blend = sigmoid(z_market + 0.10 * (z_model - z_market))
    # should be much closer to p_market than p_model
    p = blend_market_and_model(
        p_market=0.5, p_model=0.9,
        source_quality=0.0, model_agreement=0.0, horizon_weight=0.0,
    )
    assert 0.5 < p < 0.6  # mostly market, slightly pulled toward 0.9


def test_blend_listens_to_model_when_credibility_high():
    p = blend_market_and_model(
        p_market=0.5, p_model=0.9,
        source_quality=1.0, model_agreement=1.0, horizon_weight=1.0,
    )
    # credibility 0.75 -> closer to p_model
    assert p > 0.75


def test_full_blend_applies_both_guards():
    # Longshot regime with weak evidence: model tries 0.50, should be capped.
    p = full_blend(
        p_market=0.05, p_model=0.50,
        source_quality=0.2, model_agreement=0.2, horizon_weight=0.5,
    )
    # After longshot_guard: 0.10 (capped). After blend, even more
    # conservatively pulled toward 0.05.
    assert p < 0.15


def test_full_blend_favorite_no_shrink_after_blend():
    # High p_market, low credibility, model wants 0.60 -> blend goes
    # somewhere around 0.85ish, then favorites_no_shrink mixes 70/30.
    p_market, p_model = 0.92, 0.60
    p = full_blend(
        p_market=p_market, p_model=p_model,
        source_quality=0.5, model_agreement=0.5, horizon_weight=0.5,
    )
    # Should not be pulled all the way down to 0.60
    assert p > 0.8
