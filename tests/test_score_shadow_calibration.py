from __future__ import annotations

import scripts.score_shadow_calibration as scorer


def test_scores_resolved_binary_record() -> None:
    records = [
        {
            "event_ticker": "SHADOW-TEST",
            "title": "Who wins?",
            "recorded_at": "2026-05-19T00:00:00Z",
            "close_time": "2026-05-20T00:00:00Z",
            "probability_sum": 1.0,
            "response": {
                "probabilities": [
                    {"market": "Away", "probability": 0.35},
                    {"market": "Home", "probability": 0.65},
                ]
            },
        }
    ]

    scored = scorer.score_records(records, {"SHADOW-TEST": "Home"})

    assert scored == [
        {
            "event_ticker": "SHADOW-TEST",
            "status": "scored",
            "title": "Who wins?",
            "winner": "Home",
            "winner_probability": 0.65,
            "winner_brier": 0.1225,
            "multiclass_brier": 0.245,
            "probability_sum": 1.0,
            "recorded_at": "2026-05-19T00:00:00Z",
            "close_time": "2026-05-20T00:00:00Z",
        }
    ]


def test_skips_unresolved_records() -> None:
    records = [
        {
            "event_ticker": "SHADOW-TEST",
            "response": {"probabilities": [{"market": "Home", "probability": 0.65}]},
        }
    ]

    assert scorer.score_records(records, {}) == []


def test_flags_missing_winner_probability() -> None:
    records = [
        {
            "event_ticker": "SHADOW-TEST",
            "response": {"probabilities": [{"market": "Home", "probability": 0.65}]},
        }
    ]

    scored = scorer.score_records(records, {"SHADOW-TEST": "Away"})

    assert scored == [
        {
            "event_ticker": "SHADOW-TEST",
            "status": "missing_winner_probability",
            "winner": "Away",
            "markets": ["Home"],
        }
    ]
