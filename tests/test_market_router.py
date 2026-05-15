"""Market router unit tests."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import market_router


@dataclass
class _Q:
    best_bid: str = "0.4"
    best_ask: str = "0.5"
    volume_24h: str = "1000"
    ts: datetime = None


@dataclass
class _M:
    question: str
    description: str = ""
    resolution_criteria: str = ""
    topic: str = ""
    market_id: str = "m1"
    quote: _Q = None
    resolution_time: datetime = None


def _make(q, desc="", criteria="", topic=""):
    return _M(
        question=q, description=desc, resolution_criteria=criteria,
        topic=topic, quote=_Q(ts=datetime.now(timezone.utc)),
        resolution_time=datetime.now(timezone.utc) + timedelta(hours=24),
    )


def test_sports_domain():
    m = _make("Will the Lakers beat the Celtics in tonight's NBA game?")
    assert market_router.route(m)["domain"] == "sports"


def test_elections_domain():
    m = _make("Who will win the 2028 presidential election?")
    assert market_router.route(m)["domain"] == "elections"


def test_finance_domain():
    m = _make("Will the Fed cut interest rates in June?")
    assert market_router.route(m)["domain"] == "finance"


def test_weather_domain():
    m = _make("Will a hurricane make landfall in Florida this season?")
    assert market_router.route(m)["domain"] == "weather"


def test_geopolitics_domain():
    m = _make("Will Russia and Ukraine sign a ceasefire treaty by year end?")
    assert market_router.route(m)["domain"] == "geopolitics"


def test_science_tech_domain():
    m = _make("Will SpaceX launch Starship to orbit in Q3?")
    assert market_router.route(m)["domain"] == "science_tech"


def test_other_domain():
    m = _make("Will the answer be yes?")
    assert market_router.route(m)["domain"] == "other"


def test_resolution_type_threshold():
    m = _make(
        "Will revenue exceed $5 billion?",
        criteria="YES if total revenue is more than 5 billion USD.",
    )
    assert market_router.route(m)["resolution_type"] == "threshold"


def test_horizon_hours_positive():
    m = _make("anything")
    meta = market_router.route(m)
    assert meta["horizon_hours"] > 0
