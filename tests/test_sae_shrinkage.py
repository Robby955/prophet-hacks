"""Tests for forecasting.sae_shrinkage — explicit logit-additive form."""

from forecasting.sae_shrinkage import (
    CellRandomEffect,
    SAECalibrator,
    horizon_bucket,
    price_bucket,
)


def test_horizon_buckets():
    assert horizon_bucket(0.5) == "near"
    assert horizon_bucket(23) == "near"
    assert horizon_bucket(24) == "medium"
    assert horizon_bucket(24 * 7) == "long"


def test_price_buckets():
    assert price_bucket(0.05) == "p_00_10"
    assert price_bucket(0.10) == "p_10_20"
    assert price_bucket(0.45) == "p_40_50"
    assert price_bucket(0.99) == "p_90_99"


def test_cell_reliability_grows_with_n():
    c = CellRandomEffect(alpha=0.5, n=0)
    assert c.reliability(10.0) == 0.0
    c.n = 10
    assert abs(c.reliability(10.0) - 0.5) < 1e-9
    c.n = 100
    assert c.reliability(10.0) > 0.9


def test_default_calibrator_collapses_to_credibility_blend():
    """With all alphas at 0, the SAE form should produce the same
    result as a credibility-weighted blend (no random-effects
    corrections active)."""
    calib = SAECalibrator(w_model=0.50, beta_disagree=0.0)
    p = calib.predict(
        p_market=0.5,
        p_model=0.7,
        domain="sports",
        hours_to_resolution=48.0,
        model_disagreement=0.0,
    )
    # w_model=0.5 in logit space: z = 0 + 0.5*(logit(0.7) - 0) = 0.5*0.847 ~ 0.42
    # sigmoid(0.42) ~ 0.604
    assert 0.58 < p < 0.62


def test_disagreement_penalty_pulls_toward_market():
    calib = SAECalibrator(w_model=0.50, beta_disagree=0.3)
    p_no_disagree = calib.predict(
        p_market=0.5, p_model=0.8,
        domain="sports", hours_to_resolution=48.0, model_disagreement=0.0,
    )
    p_with_disagree = calib.predict(
        p_market=0.5, p_model=0.8,
        domain="sports", hours_to_resolution=48.0, model_disagreement=0.5,
    )
    # High disagreement should pull the final closer to p_market=0.5
    assert p_with_disagree < p_no_disagree
    assert p_with_disagree > 0.5


def test_update_records_residual_across_three_strata():
    calib = SAECalibrator(w_model=0.50, beta_disagree=0.0)
    # Forecast a market and miss it badly
    calib.update(
        p_market=0.4,
        p_model=0.6,
        p_final=0.50,
        outcome=1,  # actual was YES, we said 0.50
        domain="sports",
        hours_to_resolution=48.0,
    )
    snap = calib.snapshot()
    # Three cells should now have n=1
    assert snap["domain"]["sports"]["n"] == 1
    assert snap["horizon"]["medium"]["n"] == 1
    # 0.4 -> p_40_50 bucket
    assert snap["price"]["p_40_50"]["n"] == 1
    # All three should have positive alpha (we under-forecast)
    assert snap["domain"]["sports"]["alpha"] > 0
    assert snap["horizon"]["medium"]["alpha"] > 0
    assert snap["price"]["p_40_50"]["alpha"] > 0


def test_repeated_updates_shrink_residual_to_consistent_estimate():
    calib = SAECalibrator(w_model=0.50, beta_disagree=0.0)
    for _ in range(20):
        calib.update(
            p_market=0.4, p_model=0.6, p_final=0.50,
            outcome=1, domain="sports", hours_to_resolution=48.0,
        )
    snap = calib.snapshot()
    # After 20 identical residuals, the alpha should be close to
    # share = (logit(0.99) - logit(0.50)) / 3 ~ 1.53
    assert 1.0 < snap["domain"]["sports"]["alpha"] < 2.0


def test_frozen_round_trip():
    calib = SAECalibrator(w_model=0.45, beta_disagree=0.2, shrink_k=8.0)
    for _ in range(5):
        calib.update(
            p_market=0.6, p_model=0.7, p_final=0.65,
            outcome=1, domain="finance", hours_to_resolution=24.0,
        )
    snap = calib.snapshot()
    reloaded = SAECalibrator.from_frozen(snap)
    assert reloaded.w_model == 0.45
    assert reloaded.beta_disagree == 0.2
    assert reloaded.shrink_k == 8.0
    assert reloaded.domain_effects["finance"].n == 5
    p1 = calib.predict(p_market=0.6, p_model=0.7, domain="finance",
                       hours_to_resolution=24.0, model_disagreement=0.0)
    p2 = reloaded.predict(p_market=0.6, p_model=0.7, domain="finance",
                          hours_to_resolution=24.0, model_disagreement=0.0)
    assert abs(p1 - p2) < 1e-9


def test_predict_clamps_to_valid_probability():
    calib = SAECalibrator(w_model=10.0)  # absurd weight
    p = calib.predict(
        p_market=0.5, p_model=0.99,
        domain="sports", hours_to_resolution=48.0, model_disagreement=0.0,
    )
    assert 0.01 <= p <= 0.99


def test_sparse_cell_does_not_dominate_global_estimate():
    """The reliability multiplier should prevent a single noisy
    observation from swinging predictions."""
    calib = SAECalibrator(w_model=0.50, beta_disagree=0.0, shrink_k=10.0)
    # One miss in a brand-new domain
    calib.update(
        p_market=0.5, p_model=0.5, p_final=0.5,
        outcome=1, domain="new-domain", hours_to_resolution=48.0,
    )
    # Predict in that same cell: residual should barely move us
    # because reliability = 1 / (1 + 10) ~ 0.09
    p = calib.predict(
        p_market=0.5, p_model=0.5,
        domain="new-domain", hours_to_resolution=48.0, model_disagreement=0.0,
    )
    assert abs(p - 0.5) < 0.10
