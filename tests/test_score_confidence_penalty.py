"""Tests for tools.score_confidence_penalty composite scoring."""

import sys
from pathlib import Path

# Make `tools/` importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.score_confidence_penalty import (
    DEFAULT_LAMBDA_COST,
    DEFAULT_LAMBDA_FRAGILE_SOURCE,
    DEFAULT_LAMBDA_LONGSHOT,
    DEFAULT_LAMBDA_OVERCONFIDENT,
    composite_score,
    score_trace,
    summarize,
)


def test_unresolved_outcome_skips_brier_and_overconfidence():
    s = composite_score(
        p_final=0.9, p_market=0.5, outcome=None,
        model_call_cost_usd=0.0, fragile_source_count=0,
    )
    assert s.brier == 0.0
    assert s.overconfidence_penalty == 0.0
    assert s.total == 0.0


def test_correct_confident_bet_has_low_total():
    s = composite_score(
        p_final=0.95, p_market=0.5, outcome=1,
        model_call_cost_usd=0.0, fragile_source_count=0,
    )
    # brier = 0.05^2 = 0.0025; no overconfidence penalty
    assert abs(s.brier - 0.0025) < 1e-9
    assert s.overconfidence_penalty == 0.0
    assert s.total < 0.01


def test_wrong_confident_bet_has_high_overconfidence_penalty():
    s = composite_score(
        p_final=0.95, p_market=0.5, outcome=0,
        model_call_cost_usd=0.0, fragile_source_count=0,
    )
    # brier = 0.95^2 = 0.9025
    # confidence = 0.45 -> overconf = 0.5 * 0.45^2 = 0.10125
    assert s.brier > 0.9
    assert s.overconfidence_penalty > 0.10
    assert s.total > 1.0


def test_low_confidence_wrong_has_smaller_overconfidence_penalty():
    """A 0.55 forecast that misses should be penalized less harshly
    than a 0.95 forecast that misses."""
    high_conf = composite_score(
        p_final=0.95, p_market=0.5, outcome=0,
    )
    low_conf = composite_score(
        p_final=0.55, p_market=0.5, outcome=0,
    )
    assert (
        high_conf.overconfidence_penalty
        > low_conf.overconfidence_penalty
    )


def test_longshot_lift_penalized():
    no_lift = composite_score(
        p_final=0.06, p_market=0.05, outcome=0,
        p_model_raw=0.06,
    )
    with_lift = composite_score(
        p_final=0.30, p_market=0.05, outcome=0,
        p_model_raw=0.30,
    )
    assert with_lift.longshot_penalty == DEFAULT_LAMBDA_LONGSHOT
    assert no_lift.longshot_penalty == 0.0


def test_no_longshot_penalty_above_threshold():
    s = composite_score(
        p_final=0.50, p_market=0.20, outcome=0,
        p_model_raw=0.50,
    )
    # p_market >= 0.10 -> no longshot penalty regardless of lift
    assert s.longshot_penalty == 0.0


def test_cost_penalty_grows_linearly():
    a = composite_score(p_final=0.5, p_market=0.5, outcome=0, model_call_cost_usd=0.0)
    b = composite_score(p_final=0.5, p_market=0.5, outcome=0, model_call_cost_usd=1.0)
    c = composite_score(p_final=0.5, p_market=0.5, outcome=0, model_call_cost_usd=2.0)
    assert b.cost_penalty - a.cost_penalty == DEFAULT_LAMBDA_COST
    assert c.cost_penalty - b.cost_penalty == DEFAULT_LAMBDA_COST


def test_fragile_source_penalty_grows_linearly():
    a = composite_score(p_final=0.5, p_market=0.5, outcome=0, fragile_source_count=0)
    b = composite_score(p_final=0.5, p_market=0.5, outcome=0, fragile_source_count=3)
    assert b.fragile_source_penalty - a.fragile_source_penalty == 3 * DEFAULT_LAMBDA_FRAGILE_SOURCE


def test_summarize_empty_returns_n_zero():
    assert summarize([]) == {"n": 0}


def test_summarize_aggregates_means():
    scores = [
        composite_score(p_final=0.9, p_market=0.5, outcome=1),  # correct
        composite_score(p_final=0.9, p_market=0.5, outcome=0),  # wrong + confident
        composite_score(p_final=0.5, p_market=0.5, outcome=1),  # 50/50
    ]
    s = summarize(scores)
    assert s["n"] == 3
    assert s["mean_brier"] > 0


def test_score_trace_handles_missing_optional_fields():
    rows = [
        {"market_id": "m1", "p_final": 0.6, "p_market": 0.55, "outcome": 1},
        {"market_id": "m2", "p_final": 0.3, "p_market": 0.40, "outcome": 0},
    ]
    scores = score_trace(rows)
    assert len(scores) == 2
    assert scores[0].market_id == "m1"
