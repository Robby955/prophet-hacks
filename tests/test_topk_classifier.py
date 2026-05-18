"""Top-K / multi-label classifier + guard.

Per Anri Discord 2026-05-17 22:30 CT: top-K events use marginal
probabilities scored independently; the server does NOT normalize. Our
default apply_longshot_guard renormalizes when the probability vector
sums to a value in [0.5, 1.5], which would corrupt top-K marginals.

These tests pin:
  1. The classifier picks the right semantics for representative titles.
  2. apply_longshot_guard_topk floors+caps but never renormalizes.
  3. Winner-take-all keeps current behavior bit-identical.
"""

from __future__ import annotations

import forecast_track as ft


# ---------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------

def test_classifier_default_is_winner_take_all() -> None:
    event = {"title": "Will the Fed cut rates in December 2026?"}
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "winner_take_all"
    assert target_sum == 1


def test_classifier_who_wins_is_winner_take_all() -> None:
    event = {"title": "Who will win Super Bowl LXI?"}
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "winner_take_all"
    assert target_sum == 1


def test_classifier_top_k_explicit() -> None:
    event = {"title": "Which 5 of these 26 teams finish in the top 5?"}
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "top_k"
    assert target_sum == 5


def test_classifier_finish_top_4() -> None:
    event = {"title": "Which teams finish in the Premier League top 4?"}
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "top_k"
    assert target_sum == 4


def test_classifier_multi_label_qualify() -> None:
    event = {"title": "Which teams will qualify for the playoffs?"}
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "multi_label"
    assert target_sum is None


def test_classifier_multi_label_nominees() -> None:
    event = {"title": "Who will be nominated for Best Picture?"}
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "multi_label"
    assert target_sum is None


def test_classifier_ordered_threshold_over_under() -> None:
    event = {"title": "Will GDP growth be over/under 2.5% in Q4?"}
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "ordered_threshold"
    assert target_sum is None


def test_classifier_ordered_threshold_at_least() -> None:
    event = {"title": "Will the Fed cut rates at least 2 times in 2026?"}
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "ordered_threshold"
    assert target_sum is None


def test_classifier_empty_event_falls_back_to_winner_take_all() -> None:
    semantics, target_sum = ft._classify_event_semantics({})
    assert semantics == "winner_take_all"
    assert target_sum == 1


def test_classifier_reads_description_not_just_title() -> None:
    event = {
        "title": "NBA top-5 race",
        "description": "Which 5 of these 12 teams finish in the top 5 of the conference?",
    }
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "top_k"
    assert target_sum == 5


# ---------------------------------------------------------------------
# Codex review fix (2026-05-18): binary outcomes short-circuit to
# winner_take_all even when the title contains threshold language.
# ---------------------------------------------------------------------

def test_binary_yes_no_overrides_at_least_threshold_in_title() -> None:
    """Title says 'at least 2', but outcomes are Yes/No — must be WTA."""
    event = {
        "title": "Will the Fed cut rates at least 2 times in 2026?",
        "outcomes": ["Yes", "No"],
    }
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "winner_take_all"
    assert target_sum == 1


def test_binary_over_under_outcomes_are_winner_take_all() -> None:
    """Title says 'over/under', outcomes are Over/Under — binary WTA."""
    event = {
        "title": "Will GDP growth be over/under 2.5% in Q4?",
        "outcomes": ["Over", "Under"],
    }
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "winner_take_all"
    assert target_sum == 1


def test_binary_true_false_outcomes_are_winner_take_all() -> None:
    event = {
        "title": "Is it true that the S&P 500 closes above 7000 by Jan 1 2027?",
        "outcomes": ["True", "False"],
    }
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "winner_take_all"


def test_binary_above_below_outcomes_are_winner_take_all() -> None:
    event = {
        "title": "Will US CPI YoY be more than 3% in Q4?",
        "outcomes": ["Above 3%", "Below 3%"],
    }
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "winner_take_all"


def test_multi_outcome_threshold_keeps_ordered_threshold() -> None:
    """Reverse regression: a 4-outcome event with 'at least N' in title
    is NOT binary — it should still classify as ordered_threshold."""
    event = {
        "title": "How many times will the Fed cut rates in 2026? At least 1, at least 2, at least 3, or at least 4?",
        "outcomes": ["At least 1", "At least 2", "At least 3", "At least 4"],
    }
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "ordered_threshold"


def test_classifier_reads_question_field_too() -> None:
    """Some events use a `question` field instead of (or in addition to)
    title. The classifier should inspect it."""
    event = {
        "title": "NBA top-5",
        "question": "Which 5 of these 12 teams finish in the top 5?",
        "outcomes": [f"Team {i}" for i in range(12)],
    }
    semantics, target_sum = ft._classify_event_semantics(event)
    assert semantics == "top_k"
    assert target_sum == 5


# ---------------------------------------------------------------------
# apply_longshot_guard_topk — floors + caps, never renormalizes
# ---------------------------------------------------------------------

def test_topk_guard_does_not_renormalize_when_sum_in_distribution_range() -> None:
    """The bug case. Marginals summing to ~1.0 must stay intact, not
    get squashed by a sum-to-1 renormalization."""
    probs = [
        {"market": "Alpha", "probability": 0.85},
        {"market": "Beta",  "probability": 0.15},
    ]
    out = ft.apply_longshot_guard_topk(probs, n_outcomes=2)
    assert out[0]["probability"] == 0.85
    assert out[1]["probability"] == 0.15
    assert sum(p["probability"] for p in out) == 1.0  # marginals; unchanged


def test_topk_guard_does_not_renormalize_when_sum_above_one() -> None:
    """A 5-of-26 top-K event with marginals summing to ~5.0 must stay
    summed to ~5.0 after the guard."""
    # Realistic top-5 marginals: 5 favorites near 0.8, 21 longshots near 0.05.
    probs = [{"market": f"Team {i}", "probability": 0.05} for i in range(26)]
    for i in range(5):
        probs[i]["probability"] = 0.80
    sum_before = sum(p["probability"] for p in probs)
    assert 4.5 < sum_before < 5.5
    out = ft.apply_longshot_guard_topk(probs, n_outcomes=26)
    sum_after = sum(p["probability"] for p in out)
    assert abs(sum_after - sum_before) < 0.2  # only minor floor lift; no renorm
    assert out[0]["probability"] == 0.80       # favorites preserved


def test_topk_guard_floors_below_threshold() -> None:
    """Per-outcome floor still applies."""
    probs = [{"market": "Alpha", "probability": 0.001}, {"market": "Beta", "probability": 0.999}]
    out = ft.apply_longshot_guard_topk(probs, n_outcomes=2)
    assert out[0]["probability"] == ft.longshot_guard_floor(2)
    assert out[1]["probability"] == ft.P_YES_MAX  # capped at 0.99


def test_topk_guard_empty_passthrough() -> None:
    assert ft.apply_longshot_guard_topk([], 2) == []


# ---------------------------------------------------------------------
# Winner-take-all unchanged — sanity check on the existing guard
# ---------------------------------------------------------------------

def test_winner_take_all_guard_still_renormalizes() -> None:
    """Regression guard: the existing apply_longshot_guard must keep
    renormalizing when sum is in [0.5, 1.5]. If this test breaks, the
    winner-take-all path drifted."""
    probs = [
        {"market": "Yes", "probability": 0.6},
        {"market": "No",  "probability": 0.6},
    ]
    out = ft.apply_longshot_guard(probs, n_outcomes=2)
    s = sum(p["probability"] for p in out)
    assert 0.99 <= s <= 1.01  # renormalized to 1


def test_winner_take_all_guard_skips_renorm_outside_band() -> None:
    """When sum is way outside [0.5, 1.5] the existing guard does NOT
    renormalize. Top-K-like inputs already escaped the bug, but the
    bug case is sums INSIDE the band on a top-K event — that's what
    the topk variant protects."""
    probs = [{"market": f"Team {i}", "probability": 0.8} for i in range(10)]
    out = ft.apply_longshot_guard(probs, n_outcomes=10)
    s = sum(p["probability"] for p in out)
    assert s > 5.0  # sum 8.0, no renormalization happened
