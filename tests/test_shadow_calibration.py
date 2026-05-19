from __future__ import annotations

import json
from pathlib import Path

import scripts.shadow_calibration as sc


def _event(close_time: str = "2099-01-01T00:00:00Z") -> dict:
    return {
        "event_ticker": "SHADOW-TEST",
        "market_ticker": "SHADOW-TEST",
        "title": "Who will win the test game?",
        "category": "Sports",
        "close_time": close_time,
        "outcomes": ["Away", "Home"],
        "rules": "Resolve to the official winner.",
        "market_snapshot": {"notes": "test"},
    }


def test_dry_run_validates_and_does_not_write(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    out = tmp_path / "shadow.jsonl"
    events.write_text(json.dumps([_event()]))

    records = sc.run_shadow_forecasts(
        events_path=events,
        endpoint="https://example.test/predict",
        out=out,
        limit=None,
        force=False,
        include_closed=False,
        dry_run=True,
        timeout=1.0,
    )

    assert len(records) == 1
    assert records[0]["status"] == "dry_run"
    assert records[0]["request"]["outcomes"] == ["Away", "Home"]
    assert not out.exists()


def test_skip_existing_event_id(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    out = tmp_path / "shadow.jsonl"
    events.write_text(json.dumps([_event()]))
    out.write_text(json.dumps({"event_ticker": "SHADOW-TEST"}) + "\n")

    records = sc.run_shadow_forecasts(
        events_path=events,
        endpoint="https://example.test/predict",
        out=out,
        limit=None,
        force=False,
        include_closed=False,
        dry_run=True,
        timeout=1.0,
    )

    assert records == []


def test_closed_events_skip_by_default(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    out = tmp_path / "shadow.jsonl"
    events.write_text(json.dumps([_event("2000-01-01T00:00:00Z")]))

    records = sc.run_shadow_forecasts(
        events_path=events,
        endpoint="https://example.test/predict",
        out=out,
        limit=None,
        force=False,
        include_closed=False,
        dry_run=True,
        timeout=1.0,
    )

    assert records == []
