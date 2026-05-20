"""Unit tests for the pure logic in scripts/diagnostics.py.

These exercise the measurement primitives — probability-vector
reconstruction, the reliability diagram / ECE, the stratifiers, and the
prediction-to-resolved join — against tiny synthetic inputs whose answers
are hand-checkable. No network, and file I/O is confined to ``tmp_path``.
"""

from __future__ import annotations

import json

import pytest

import scripts.diagnostics as diag


# ---------------------------------------------------------------------------
# _prob_vector
# ---------------------------------------------------------------------------
def test_prob_vector_from_probabilities_list_orders_by_outcomes() -> None:
    row = {
        "probabilities": [
            {"market": "Home", "probability": 0.65},
            {"market": "Away", "probability": 0.35},
        ]
    }
    assert diag._prob_vector(row, ["Away", "Home"]) == pytest.approx([0.35, 0.65])


def test_prob_vector_missing_outcome_is_zero() -> None:
    row = {"probabilities": [{"market": "Home", "probability": 0.7}]}
    assert diag._prob_vector(row, ["Home", "Draw", "Away"]) == pytest.approx(
        [0.7, 0.0, 0.0]
    )


def test_prob_vector_binary_fallback_splits_remainder() -> None:
    # No probabilities list: p_yes feeds outcome[0], rest split evenly.
    row = {"p_yes": 0.6}
    assert diag._prob_vector(row, ["Yes", "No"]) == pytest.approx([0.6, 0.4])

    row3 = {"p_yes": 0.5}
    assert diag._prob_vector(row3, ["A", "B", "C"]) == pytest.approx([0.5, 0.25, 0.25])


def test_prob_vector_single_outcome_fallback_returns_p_yes() -> None:
    assert diag._prob_vector({"p_yes": 0.8}, ["Only"]) == pytest.approx([0.8])


def test_prob_vector_fallback_defaults_to_uniform_when_no_p_yes() -> None:
    # Missing p_yes -> 1/len(outcomes) for outcome[0].
    assert diag._prob_vector({}, ["A", "B", "C", "D"]) == pytest.approx(
        [0.25, 0.25, 0.25, 0.25]
    )


# ---------------------------------------------------------------------------
# reliability
# ---------------------------------------------------------------------------
def test_reliability_single_bin_ece_is_hand_checkable() -> None:
    # Four preds all at 0.9 (bin 9 with n_bins=10), half resolved true.
    records = [
        {"p_yes": 0.9, "actual0": 1},
        {"p_yes": 0.9, "actual0": 1},
        {"p_yes": 0.9, "actual0": 0},
        {"p_yes": 0.9, "actual0": 0},
    ]
    out = diag.reliability(records, n_bins=10)

    assert out["n"] == 4
    assert len(out["bins"]) == 10

    bin9 = out["bins"][9]
    assert bin9["count"] == 4
    assert bin9["mean_pred"] == pytest.approx(0.9)
    assert bin9["emp_freq"] == pytest.approx(0.5)

    # Every other bin is empty.
    assert all(b["count"] == 0 for b in out["bins"] if b["bin"] != 9)
    assert all(out["bins"][i]["mean_pred"] is None for i in range(9))

    # Single populated bin holds all weight: ECE = |0.9 - 0.5| = 0.4.
    assert out["ece"] == pytest.approx(0.4)


def test_reliability_ece_is_count_weighted_mean_of_gaps() -> None:
    # Two distinct bins with different counts -> count-weighted ECE.
    # bin5 (0.55): 3 preds, all actual=1 -> gap |0.55 - 1.0| = 0.45
    # bin9 (0.95): 1 pred, actual=0    -> gap |0.95 - 0.0| = 0.95
    records = [
        {"p_yes": 0.55, "actual0": 1},
        {"p_yes": 0.55, "actual0": 1},
        {"p_yes": 0.55, "actual0": 1},
        {"p_yes": 0.95, "actual0": 0},
    ]
    out = diag.reliability(records, n_bins=10)

    assert out["bins"][5]["count"] == 3
    assert out["bins"][5]["mean_pred"] == pytest.approx(0.55)
    assert out["bins"][5]["emp_freq"] == pytest.approx(1.0)
    assert out["bins"][9]["count"] == 1
    assert out["bins"][9]["emp_freq"] == pytest.approx(0.0)

    expected = (3 / 4) * 0.45 + (1 / 4) * 0.95
    assert out["ece"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# stratify
# ---------------------------------------------------------------------------
def test_stratify_groups_and_sorts_worst_first() -> None:
    records = [
        {"category": "Sports", "binary_brier": 0.1, "multi_brier": 0.1,
         "confidence": 0.9, "winner_prob": 0.9},
        {"category": "Sports", "binary_brier": 0.3, "multi_brier": 0.3,
         "confidence": 0.7, "winner_prob": 0.7},
        {"category": "Finance", "binary_brier": 0.5, "multi_brier": 0.6,
         "confidence": 0.6, "winner_prob": 0.4},
    ]
    out = diag.stratify(records, "category")

    groups = {g["group"]: g for g in out}
    assert set(groups) == {"Sports", "Finance"}
    assert groups["Sports"]["n"] == 2
    assert groups["Sports"]["binary_brier"] == pytest.approx(0.2)  # mean(0.1, 0.3)
    assert groups["Finance"]["binary_brier"] == pytest.approx(0.5)

    # Worst (highest binary Brier) comes first.
    assert [g["group"] for g in out] == ["Finance", "Sports"]


def test_stratify_breaks_ties_by_larger_n() -> None:
    records = [
        {"category": "A", "binary_brier": 0.2, "multi_brier": 0.2,
         "confidence": 0.5, "winner_prob": 0.5},
        {"category": "B", "binary_brier": 0.2, "multi_brier": 0.2,
         "confidence": 0.5, "winner_prob": 0.5},
        {"category": "B", "binary_brier": 0.2, "multi_brier": 0.2,
         "confidence": 0.5, "winner_prob": 0.5},
    ]
    out = diag.stratify(records, "category")
    # Equal Brier -> larger group (B, n=2) sorts ahead of A (n=1).
    assert [g["group"] for g in out] == ["B", "A"]


# ---------------------------------------------------------------------------
# conf_buckets
# ---------------------------------------------------------------------------
def test_conf_buckets_assigns_events_to_correct_edges() -> None:
    records = [
        {"confidence": 0.55, "binary_brier": 0.1, "winner_prob": 0.55},  # 0.5-0.6
        {"confidence": 0.75, "binary_brier": 0.2, "winner_prob": 0.75},  # 0.7-0.8
        {"confidence": 1.0, "binary_brier": 0.0, "winner_prob": 1.0},    # 0.9-1.0
    ]
    out = diag.conf_buckets(records)

    by_group = {b["group"]: b for b in out}
    assert by_group["0.5-0.6"]["n"] == 1
    assert by_group["0.5-0.6"]["binary_brier"] == pytest.approx(0.1)
    assert by_group["0.7-0.8"]["n"] == 1
    assert by_group["0.7-0.8"]["mean_winner_prob"] == pytest.approx(0.75)
    assert by_group["0.9-1.0"]["n"] == 1
    assert by_group["0.9-1.0"]["mean_winner_prob"] == pytest.approx(1.0)

    # Buckets that received nothing report n=0 and None Brier.
    assert by_group["0.6-0.7"]["n"] == 0
    assert by_group["0.6-0.7"]["binary_brier"] is None
    assert by_group["0.8-0.9"]["n"] == 0

    # The full edge set [0.5..1.0001] yields five buckets.
    assert len(out) == len(diag.CONF_EDGES) - 1


def test_conf_buckets_boundary_is_inclusive_lower_exclusive_upper() -> None:
    # 0.6 belongs to 0.6-0.7 (lo <= conf < hi), not to 0.5-0.6.
    records = [{"confidence": 0.6, "binary_brier": 0.25, "winner_prob": 0.6}]
    by_group = {b["group"]: b for b in diag.conf_buckets(records)}
    assert by_group["0.5-0.6"]["n"] == 0
    assert by_group["0.6-0.7"]["n"] == 1


# ---------------------------------------------------------------------------
# build_records
# ---------------------------------------------------------------------------
def _write_json(path, obj) -> str:
    path.write_text(json.dumps(obj))
    return str(path)


def test_build_records_joins_and_computes_briers(tmp_path) -> None:
    pred_path = _write_json(
        tmp_path / "preds.json",
        {
            "predictions": [
                {
                    "market_ticker": "BIN-1",
                    "probabilities": [
                        {"market": "Yes", "probability": 0.7},
                        {"market": "No", "probability": 0.3},
                    ],
                },
                {
                    "market_ticker": "MULTI-1",
                    "probabilities": [
                        {"market": "A", "probability": 0.5},
                        {"market": "B", "probability": 0.3},
                        {"market": "C", "probability": 0.2},
                    ],
                },
            ]
        },
    )
    resolved_path = _write_json(
        tmp_path / "resolved.json",
        [
            {
                "market_ticker": "BIN-1",
                "category": "Sports",
                "outcomes": ["Yes", "No"],
                "resolved_outcome": {"value": ["Yes"]},
            },
            {
                "market_ticker": "MULTI-1",
                "category": "Politics",
                "outcomes": ["A", "B", "C"],
                "resolved_outcome": {"value": ["B"]},
            },
        ],
    )

    records = diag.build_records(pred_path, resolved_path)
    assert len(records) == 2
    by_ticker = {r["ticker"]: r for r in records}

    binr = by_ticker["BIN-1"]
    assert binr["kind"] == "binary"
    assert binr["n_outcomes"] == 2
    assert binr["category"] == "Sports"
    assert binr["actual0"] == 1  # winner is outcome[0]
    assert binr["p_yes"] == pytest.approx(0.7)
    # binary Brier on outcome[0]: (0.7 - 1)^2 = 0.09
    assert binr["binary_brier"] == pytest.approx(0.09)
    # multiclass Brier: (0.7-1)^2 + (0.3-0)^2 = 0.18
    assert binr["multi_brier"] == pytest.approx(0.18)
    assert binr["winner_prob"] == pytest.approx(0.7)
    assert binr["confidence"] == pytest.approx(0.7)

    mult = by_ticker["MULTI-1"]
    assert mult["kind"] == "multi"
    assert mult["n_outcomes"] == 3
    assert mult["actual0"] == 0  # winner "B" is not outcome[0]
    # binary view: outcome[0]="A" at 0.5, actual0=0 -> (0.5-0)^2 = 0.25
    assert mult["binary_brier"] == pytest.approx(0.25)
    # multiclass: (0.5-0)^2 + (0.3-1)^2 + (0.2-0)^2 = 0.25+0.49+0.04 = 0.78
    assert mult["multi_brier"] == pytest.approx(0.78)
    assert mult["winner_prob"] == pytest.approx(0.3)  # prob on "B"
    assert mult["confidence"] == pytest.approx(0.5)  # max of the vector


def test_build_records_uses_event_ticker_key_and_skips_unmatched(tmp_path) -> None:
    pred_path = _write_json(
        tmp_path / "preds.json",
        [
            {
                "event_ticker": "EV-1",
                "probabilities": [
                    {"market": "Yes", "probability": 0.4},
                    {"market": "No", "probability": 0.6},
                ],
            }
        ],
    )
    resolved_path = _write_json(
        tmp_path / "resolved.json",
        {
            "EV-1": {
                "market_ticker": "EV-1",
                "outcomes": ["Yes", "No"],
                "resolved_outcome": {"value": ["No"]},
            },
            # Resolved but no matching prediction -> skipped.
            "EV-MISSING": {
                "market_ticker": "EV-MISSING",
                "outcomes": ["Yes", "No"],
                "resolved_outcome": {"value": ["Yes"]},
            },
        },
    )

    records = diag.build_records(pred_path, resolved_path)
    assert len(records) == 1
    rec = records[0]
    assert rec["ticker"] == "EV-1"
    assert rec["category"] == "Uncategorized"  # default when missing
    assert rec["actual0"] == 0  # winner "No" is outcome[1]


def test_build_records_skips_events_with_no_winner(tmp_path) -> None:
    pred_path = _write_json(
        tmp_path / "preds.json",
        [{"market_ticker": "EV-1", "probabilities": [
            {"market": "Yes", "probability": 0.4},
            {"market": "No", "probability": 0.6}]}],
    )
    resolved_path = _write_json(
        tmp_path / "resolved.json",
        [{"market_ticker": "EV-1", "outcomes": ["Yes", "No"],
          "resolved_outcome": {}}],  # unresolved
    )
    assert diag.build_records(pred_path, resolved_path) == []


# ---------------------------------------------------------------------------
# find_largest_residual
# ---------------------------------------------------------------------------
def test_find_largest_residual_mentions_ece() -> None:
    records = [
        {"category": "Sports", "kind": "binary", "p_yes": 0.9, "actual0": 1,
         "confidence": 0.9, "winner_prob": 0.9, "binary_brier": 0.01,
         "multi_brier": 0.02, "n_outcomes": 2},
        {"category": "Sports", "kind": "binary", "p_yes": 0.9, "actual0": 0,
         "confidence": 0.9, "winner_prob": 0.1, "binary_brier": 0.81,
         "multi_brier": 1.62, "n_outcomes": 2},
    ]
    report = diag.analyze(records, "synthetic")
    headline = diag.find_largest_residual(report)

    assert isinstance(headline, str)
    assert "ECE" in headline
    # Both preds at 0.9 land in bin 9; emp_freq=0.5 -> ECE = |0.9 - 0.5| = 0.4.
    assert report["reliability"]["ece"] == pytest.approx(0.4)
    assert "0.400" in headline
