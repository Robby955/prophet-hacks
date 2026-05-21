"""Unit tests for the pure ticker-parsing logic in auto_resolve_finance.

Covers ``parse_spec`` only. No network: ``parse_spec`` derives the resolver
spec entirely from the event ticker string and never fetches market data.
"""

from __future__ import annotations

from datetime import date

import scripts.auto_resolve_finance as fin


def _event(ticker: str, close_time: str | None = "2026-05-19T20:00:00Z") -> dict:
    return {"event_ticker": ticker, "close_time": close_time}


def test_parse_equity_close_above_threshold() -> None:
    spec = fin.parse_spec(_event("SHADOW-FIN-SPY-CLOSE-ABOVE-739-20260519"))
    assert spec is not None
    assert spec["asset"] == "equity"
    assert spec["symbol"] == "SPY"
    assert spec["kind"] == "gt"
    assert spec["threshold"] == 739.0
    assert spec["event_date"] == date(2026, 5, 19)
    assert spec["close_time"] == "2026-05-19T20:00:00Z"


def test_parse_crypto_ge_threshold() -> None:
    spec = fin.parse_spec(_event("SHADOW-CRYPTO-BTC-GE-77500-20260531"))
    assert spec is not None
    assert spec["asset"] == "crypto"
    assert spec["symbol"] == "BTC"
    assert spec["kind"] == "ge"
    assert spec["threshold"] == 77500.0
    assert spec["event_date"] == date(2026, 5, 31)


def test_parse_up_after_event_is_next_day_up() -> None:
    spec = fin.parse_spec(_event("SHADOW-FIN-TLT-UP-AFTER-FOMC-MINUTES-20260520"))
    assert spec is not None
    assert spec["asset"] == "equity"
    assert spec["symbol"] == "TLT"
    assert spec["kind"] == "next_day_up"
    assert spec["threshold"] is None
    assert spec["event_date"] == date(2026, 5, 20)


def test_parse_next_day_up_token() -> None:
    spec = fin.parse_spec(_event("SHADOW-FIN-NVDA-NEXT-DAY-UP-20260521"))
    assert spec is not None
    assert spec["symbol"] == "NVDA"
    assert spec["kind"] == "next_day_up"
    assert spec["threshold"] is None
    assert spec["event_date"] == date(2026, 5, 21)


def test_non_shadow_ticker_returns_none() -> None:
    assert fin.parse_spec(_event("KXNFL-25-DAL-WIN-20260519")) is None


def test_unknown_domain_returns_none() -> None:
    assert fin.parse_spec(_event("SHADOW-WEATHER-CHI-ABOVE-90-20260519")) is None


def test_malformed_date_returns_none() -> None:
    assert fin.parse_spec(_event("SHADOW-FIN-SPY-ABOVE-739-2026XXYY")) is None


def test_too_few_parts_returns_none() -> None:
    assert fin.parse_spec(_event("SHADOW-FIN-SPY")) is None


def test_no_recognized_kind_returns_none() -> None:
    # No UP / GE / ABOVE / BELOW / BETWEEN token -> unparseable kind.
    assert fin.parse_spec(_event("SHADOW-FIN-SPY-SIDEWAYS-739-20260519")) is None


def test_below_parses_as_lt() -> None:
    spec = fin.parse_spec(_event("SHADOW-FIN-SPY-CLOSE-BELOW-730-20260522"))
    assert spec is not None and spec["kind"] == "lt" and spec["threshold"] == 730.0


def test_between_parses_as_interval() -> None:
    spec = fin.parse_spec(_event("SHADOW-FIN-SPY-BETWEEN-731-735-20260522"))
    assert spec is not None and spec["kind"] == "between"
    assert spec["threshold"] == 731.0 and spec["hi"] == 735.0


def test_winner_for_below_and_between() -> None:
    assert fin._winner_for("lt", 720.0, 730.0) == "Yes"
    assert fin._winner_for("lt", 740.0, 730.0) == "No"
    assert fin._winner_for("between", 733.0, 731.0, 735.0) == "Yes"
    assert fin._winner_for("between", 736.0, 731.0, 735.0) == "No"


def test_empty_or_missing_ticker_returns_none() -> None:
    assert fin.parse_spec({"close_time": "2026-05-19T20:00:00Z"}) is None
    assert fin.parse_spec({"event_ticker": ""}) is None
