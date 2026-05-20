"""Unit tests for the deterministic slate-building logic in
generate_sports_slate.

``slate_for`` is the only meaningful unit, and its ESPN parsing /
ticker-and-label construction is pure given a payload. We never hit the
network: ``_http_get`` is monkeypatched to return a canned ESPN scoreboard so
only the parsing logic runs.
"""

from __future__ import annotations

import json

import scripts.generate_sports_slate as gen


def test_module_constants_present() -> None:
    assert gen.LEAGUE_PATHS["MLB"] == "baseball/mlb"
    assert set(gen.LEAGUE_NOUN) == set(gen.LEAGUE_PATHS)


def _payload() -> dict:
    return {
        "events": [
            {
                "date": "2026-05-20T23:05:00Z",
                "links": [{"href": "https://espn.com/game/1"}],
                "competitions": [
                    {
                        "date": "2026-05-20T23:05:00Z",
                        "status": {"type": {"name": "STATUS_SCHEDULED"}},
                        "competitors": [
                            {
                                "homeAway": "away",
                                "team": {"displayName": "Atlanta Braves", "abbreviation": "ATL"},
                            },
                            {
                                "homeAway": "home",
                                "team": {"displayName": "Miami Marlins", "abbreviation": "MIA"},
                            },
                        ],
                    }
                ],
            },
            {
                # Already final -> excluded to keep the slate leakage-free.
                "competitions": [
                    {
                        "status": {"type": {"name": "STATUS_FINAL"}},
                        "competitors": [
                            {"homeAway": "away", "team": {"displayName": "A", "abbreviation": "A"}},
                            {"homeAway": "home", "team": {"displayName": "B", "abbreviation": "B"}},
                        ],
                    }
                ]
            },
        ]
    }


def test_slate_for_builds_ticker_and_outcome_labels(monkeypatch) -> None:
    monkeypatch.setattr(gen, "_http_get", lambda url, timeout=20: json.dumps(_payload()))

    out = gen.slate_for("MLB", "20260520")

    assert len(out) == 1  # the final game is dropped
    ev = out[0]
    assert ev["event_ticker"] == "SHADOW-MLB-ATL-MIA-20260520"
    assert ev["market_ticker"] == ev["event_ticker"]
    assert ev["outcomes"] == ["Atlanta Braves", "Miami Marlins"]
    assert ev["close_time"] == "2026-05-20T23:05:00Z"
    assert ev["category"] == "Sports"
    assert "Atlanta Braves at Miami Marlins" in ev["title"]


def test_slate_for_skips_games_with_missing_team_fields(monkeypatch) -> None:
    payload = {
        "events": [
            {
                "competitions": [
                    {
                        "date": "2026-05-20T23:05:00Z",
                        "status": {"type": {"name": "STATUS_SCHEDULED"}},
                        "competitors": [
                            # away missing abbreviation -> game skipped
                            {"homeAway": "away", "team": {"displayName": "Atlanta Braves"}},
                            {
                                "homeAway": "home",
                                "team": {"displayName": "Miami Marlins", "abbreviation": "MIA"},
                            },
                        ],
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(gen, "_http_get", lambda url, timeout=20: json.dumps(payload))

    assert gen.slate_for("MLB", "20260520") == []


def test_slate_for_empty_payload(monkeypatch) -> None:
    monkeypatch.setattr(gen, "_http_get", lambda url, timeout=20: json.dumps({"events": []}))
    assert gen.slate_for("NBA", "20260520") == []
