from __future__ import annotations

import json
from pathlib import Path

import scripts.render_shadow_dashboard as dash


def test_render_dashboard_includes_logged_forecast_and_score(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    log = tmp_path / "shadow.jsonl"
    resolutions = tmp_path / "resolutions.json"
    events.write_text(
        json.dumps(
            [
                {
                    "event_ticker": "SHADOW-TEST",
                    "title": "Who wins?",
                    "category": "Sports",
                    "close_time": "2099-01-01T00:00:00Z",
                    "outcomes": ["Away", "Home"],
                }
            ]
        )
    )
    log.write_text(
        json.dumps(
            {
                "event_ticker": "SHADOW-TEST",
                "recorded_at": "2026-05-19T00:00:00Z",
                "close_time": "2099-01-01T00:00:00Z",
                "response": {
                    "probabilities": [
                        {"market": "Away", "probability": 0.35},
                        {"market": "Home", "probability": 0.65},
                    ],
                    "rationale": "test rationale",
                },
            }
        )
        + "\n"
    )
    resolutions.write_text(json.dumps({"SHADOW-TEST": "Home"}))

    html = dash.render_dashboard(events_path=events, log_path=log, resolutions_path=resolutions)

    assert "Shadow Calibration" in html
    assert "Who wins?" in html
    assert "Home" in html
    assert "0.1225" in html
    assert "test rationale" in html


def test_render_dashboard_marks_resolved_without_forecast(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    log = tmp_path / "shadow.jsonl"
    resolutions = tmp_path / "resolutions.json"
    events.write_text(
        json.dumps(
            [
                {
                    "event_ticker": "SHADOW-NO-FORECAST",
                    "title": "Resolved but not logged",
                    "close_time": "2000-01-01T00:00:00Z",
                    "outcomes": ["Away", "Home"],
                }
            ]
        )
    )
    log.write_text("")
    resolutions.write_text(json.dumps({"SHADOW-NO-FORECAST": "Home"}))

    html = dash.render_dashboard(events_path=events, log_path=log, resolutions_path=resolutions)

    assert "resolved no forecast" in html
