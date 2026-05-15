"""Ensemble unit tests."""
import math

import ensemble


def test_ensemble_prob_median_of_symmetric_inputs():
    # median([0.3, 0.5, 0.7]) in logit space rounds-trips to 0.5
    p = ensemble.ensemble_prob([0.3, 0.5, 0.7])
    assert math.isclose(p, 0.5, abs_tol=1e-9)


def test_ensemble_prob_single_value():
    assert math.isclose(ensemble.ensemble_prob([0.42]), 0.42)


def test_ensemble_prob_empty_is_half():
    assert ensemble.ensemble_prob([]) == 0.5


def test_disagreement_penalty_high():
    stdev = ensemble.disagreement_penalty([0.2, 0.8])
    assert stdev > 0.12


def test_disagreement_penalty_low():
    stdev = ensemble.disagreement_penalty([0.55, 0.60, 0.58])
    assert stdev < 0.05


def test_combine_with_disagreement_triggers_shrink():
    market_p = 0.5
    result = ensemble.combine_with_disagreement([0.1, 0.9], market_p)
    assert result["high_disagreement"] is True
    # Recommended should be much closer to market than to median.
    assert abs(result["recommended"] - market_p) < abs(result["p_median"] - market_p) + 1e-9


def test_combine_low_disagreement_passthrough():
    market_p = 0.5
    result = ensemble.combine_with_disagreement([0.70, 0.72, 0.68], market_p)
    assert result["high_disagreement"] is False
    assert math.isclose(result["recommended"], result["p_median"], abs_tol=1e-9)


def test_should_call_strong_gating():
    # Triage barely off market -> do not call strong.
    assert ensemble.should_call_strong(p_triage=0.52, market_p=0.50) is False
    # Triage clearly off market -> call strong.
    assert ensemble.should_call_strong(p_triage=0.70, market_p=0.50) is True


def test_should_call_second_strong_higher_bar():
    # 0.10 edge: strong fires but second-strong does not (1.5x gate is 0.12).
    assert ensemble.should_call_strong(0.60, 0.50) is True
    assert ensemble.should_call_second_strong(0.60, 0.50) is False
    # 0.15 edge passes the 1.5x multiplier.
    assert ensemble.should_call_second_strong(0.65, 0.50) is True
