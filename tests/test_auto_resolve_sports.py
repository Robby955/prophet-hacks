"""Unit tests for the pure logic in auto_resolve_sports.

Covers ``parse_spec`` (ticker -> league/date/outcomes), ``_norm`` (label
normalization), and ``match_game`` (matching a fake ESPN game against outcome
labels). No network: ``match_game`` operates on hand-built game dicts and the
others are pure string/date logic.
"""

from __future__ import annotations

from datetime import date

import scripts.auto_resolve_sports as spt


def _game(
    home_name: str,
    away_name: str,
    home_abbr: str = "",
    away_abbr: str = "",
    status: str = "STATUS_FINAL",
) -> dict:
    return {
        "status": status,
        "teams": [
            {"name": away_name, "abbr": away_abbr, "score": "3", "winner": False},
            {"name": home_name, "abbr": home_abbr, "score": "5", "winner": True},
        ],
    }


# --------------------------------------------------------------------------
# _norm
# --------------------------------------------------------------------------
def test_norm_lowercases_and_strips_punctuation_and_spaces() -> None:
    assert spt._norm("  Atlanta Braves!! ") == "atlantabraves"
    assert spt._norm("St. Louis Cardinals") == "stlouiscardinals"
    assert spt._norm("Real Madrid C.F.") == "realmadridcf"


def test_norm_handles_empty_and_none() -> None:
    assert spt._norm("") == ""
    assert spt._norm(None) == ""  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# parse_spec
# --------------------------------------------------------------------------
def test_parse_mlb_spec() -> None:
    spec = spt.parse_spec(
        {
            "event_ticker": "SHADOW-MLB-ATL-MIA-20260519",
            "outcomes": ["Atlanta Braves", "Miami Marlins"],
            "close_time": "2026-05-19T23:05:00Z",
        }
    )
    assert spec is not None
    assert spec["league"] == "MLB"
    assert spec["date"] == date(2026, 5, 19)
    assert spec["outcomes"] == ["Atlanta Braves", "Miami Marlins"]
    assert spec["close_time"] == "2026-05-19T23:05:00Z"


def test_parse_soccer_spec_allowed() -> None:
    spec = spt.parse_spec(
        {"event_ticker": "SHADOW-SOCCER-RMA-FCB-20260519", "outcomes": ["Real Madrid", "Barcelona"]}
    )
    assert spec is not None
    assert spec["league"] == "SOCCER"


def test_parse_non_sports_league_returns_none() -> None:
    assert (
        spt.parse_spec(
            {"event_ticker": "SHADOW-NFL-DAL-PHI-20260519", "outcomes": ["Cowboys", "Eagles"]}
        )
        is None
    )


def test_parse_non_shadow_returns_none() -> None:
    assert (
        spt.parse_spec({"event_ticker": "KXMLB-ATL-MIA-20260519", "outcomes": ["A", "B"]}) is None
    )


def test_parse_bad_date_returns_none() -> None:
    assert (
        spt.parse_spec({"event_ticker": "SHADOW-MLB-ATL-MIA-NOTADATE", "outcomes": ["A", "B"]})
        is None
    )


def test_parse_fewer_than_two_outcomes_returns_none() -> None:
    assert (
        spt.parse_spec({"event_ticker": "SHADOW-MLB-ATL-MIA-20260519", "outcomes": ["solo"]})
        is None
    )


# --------------------------------------------------------------------------
# match_game
# --------------------------------------------------------------------------
def test_match_game_by_full_names() -> None:
    games = [
        _game("Boston Red Sox", "New York Yankees"),
        _game("Miami Marlins", "Atlanta Braves"),
    ]
    matched = spt.match_game(games, ["Atlanta Braves", "Miami Marlins"])
    assert matched is games[1]


def test_match_game_returns_none_when_no_team_pair_matches() -> None:
    games = [_game("Boston Red Sox", "New York Yankees")]
    assert spt.match_game(games, ["Atlanta Braves", "Miami Marlins"]) is None


def test_match_game_falls_back_to_abbreviations() -> None:
    games = [_game("Atlanta Braves", "Miami Marlins", home_abbr="ATL", away_abbr="MIA")]
    matched = spt.match_game(games, ["ATL", "MIA"])
    assert matched is games[0]


def test_match_game_empty_outcomes_with_no_abbrs_returns_none() -> None:
    # The name path is guarded by `if want` so empty outcomes never match on
    # names; with no abbreviations present the abbr fallback also yields nothing.
    games = [_game("Atlanta Braves", "Miami Marlins")]  # abbr defaults to ""
    assert spt.match_game(games, []) is None
