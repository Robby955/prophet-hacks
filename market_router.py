"""Market router. Classify a market by domain using regex + keyword lookup.

No LLM call. Deterministic. Cheap. Runs first in the pipeline so the rest
of the system can adjust behaviour by domain (retrieval policy, source
preferences, horizon-aware caps, etc.).

Domains:
    sports       - games, scores, championships, individual athletes
    finance      - prices, indices, IPOs, rate decisions, earnings
    weather      - temperature, precipitation, hurricanes
    elections    - presidential, congressional, primaries, referenda
    science_tech - product launches, model releases, scientific milestones
    geopolitics  - wars, treaties, sanctions, diplomatic events
    other        - anything that did not match a domain

Resolution type:
    binary    - simple YES/NO on a discrete event
    threshold - YES if some metric crosses a number
    range     - YES if a value falls in a range
    other     - everything else
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


# Keyword lookups. Lower-case. Each entry is matched with word boundaries
# (or sub-word patterns are listed as explicit regexes) so that words like
# "ukraine" do NOT match the keyword "rain". A multi-word phrase is matched
# as a literal phrase with word boundaries on either side.
_DOMAIN_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        "sports",
        [
            "nba", "nfl", "nhl", "mlb", "fifa", "ufc", "wnba", "ncaa",
            "premier league", "champions league", "super bowl", "world cup",
            "stanley cup", "world series", "olympics", "grand slam",
            "wimbledon", "us open", "tennis", "soccer", "football",
            "baseball", "basketball", "hockey", "boxing", "golf",
            "playoffs", "match", "game", "tournament", "championship",
            "winner of", "beat", "defeat", "score", "goals",
            "lakers", "celtics",
        ],
    ),
    (
        "elections",
        [
            "election", "elected", "primary", "primaries", "caucus",
            "president", "presidential", "vice president", "senate",
            "senator", "congress", "house of representatives",
            "governor", "mayor", "referendum", "ballot", "vote",
            "voter", "polls close", "electoral",
        ],
    ),
    (
        "finance",
        [
            "stock", "share price", "market cap", "ipo", "earnings",
            "revenue", "dividend", "fed", "federal reserve", "rate hike",
            "rate cut", "interest rate", "inflation", "cpi", "ppi",
            "gdp", "unemployment", "jobs report", "nasdaq", "s&p",
            "dow jones", "nyse", "bond yield", "treasury", "currency",
            "exchange rate", "bitcoin", "ethereum", "btc", "eth",
            "crypto", "merger", "acquisition", "buyback",
        ],
    ),
    (
        "weather",
        [
            "temperature", "rainfall", "snowfall", "snow", "rain",
            "hurricane", "tornado", "blizzard", "heatwave",
            "drought", "wildfire", "noaa", "celsius",
            "fahrenheit", "tropical depression", "tropical storm",
        ],
    ),
    (
        "geopolitics",
        [
            "war", "ceasefire", "treaty", "sanction", "embargo",
            "invasion", "withdraw troops", "nato", "un security council",
            "summit", "diplomatic", "ambassador", "border", "annexation",
            "missile", "nuclear", "icbm", "russia", "ukraine", "israel",
            "iran", "china", "taiwan",
        ],
    ),
    (
        "science_tech",
        [
            "launch", "spacex", "starship", "nasa", "satellite",
            "model release", "open source", "release date", "patch",
            "version", "ai model", "gpt", "claude", "gemini",
            "transformer", "benchmark", "arxiv", "nobel prize",
            "fda approval", "clinical trial", "vaccine", "patent",
            "chip", "gpu", "semiconductor", "fusion",
        ],
    ),
]


# Pre-compile a single regex per domain that matches any of its keywords
# with word boundaries on both sides. Multi-word phrases match as literals
# with word boundaries; single-character escapes are applied.
def _compile_domain_regex(keywords: list[str]) -> re.Pattern:
    parts = [re.escape(kw) for kw in keywords]
    pattern = r"(?:^|\W)(?:" + "|".join(parts) + r")(?=\W|$)"
    return re.compile(pattern, re.I)


_DOMAIN_REGEXES: list[tuple[str, re.Pattern]] = [
    (name, _compile_domain_regex(kws)) for name, kws in _DOMAIN_KEYWORDS
]


# Resolution-type heuristics on top of the criteria text.
_THRESHOLD_PATTERNS = [
    re.compile(r"\b(at least|more than|over|above|exceed[s]?|>=|>|<=|<)\b", re.I),
    re.compile(r"\b(reach|cross|surpass|hit)\b", re.I),
    re.compile(r"\b\d+(\.\d+)?\s*(%|percent|points?|million|billion|trillion)\b", re.I),
]
_RANGE_PATTERNS = [
    re.compile(r"\bbetween\s+\d", re.I),
    re.compile(r"\b\d+\s*-\s*\d+\b"),
]


def _normalize(text: str) -> str:
    if not text:
        return ""
    return text.lower()


def _classify_domain(text: str) -> str:
    text_norm = _normalize(text)
    if not text_norm:
        return "other"
    # Geopolitics is checked before weather elsewhere only for tie-breaking;
    # ordering inside _DOMAIN_KEYWORDS reflects priority of the live tracker.
    for domain, rx in _DOMAIN_REGEXES:
        if rx.search(text_norm):
            return domain
    return "other"


def _classify_resolution_type(criteria: str) -> str:
    text = criteria or ""
    for rx in _RANGE_PATTERNS:
        if rx.search(text):
            return "range"
    for rx in _THRESHOLD_PATTERNS:
        if rx.search(text):
            return "threshold"
    return "binary"


def _horizon_hours(market) -> float:
    """Hours from now until resolution, clamped at zero."""
    res = getattr(market, "resolution_time", None)
    if res is None:
        return 0.0
    if isinstance(res, datetime):
        res_dt = res
    else:
        try:
            res_dt = datetime.fromisoformat(str(res))
        except ValueError:
            return 0.0
    if res_dt.tzinfo is None:
        res_dt = res_dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = (res_dt - now).total_seconds() / 3600.0
    return max(0.0, delta)


def _market_text(market) -> str:
    """Concatenate question + description + resolution criteria for matching."""
    parts: list[str] = []
    for field in ("question", "title", "description", "resolution_criteria",
                   "resolution_description", "topic"):
        val = getattr(market, field, None)
        if isinstance(val, str) and val:
            parts.append(val)
    return " \n ".join(parts)


def route(market: Any) -> dict:
    """Classify a market.

    Returns a dict with the domain, the time horizon in hours, and the
    resolution type. The dict is appended to the working candidate state.
    """
    full_text = _market_text(market)
    criteria = (
        getattr(market, "resolution_criteria", None)
        or getattr(market, "resolution_description", None)
        or ""
    )
    return {
        "domain": _classify_domain(full_text),
        "horizon_hours": _horizon_hours(market),
        "resolution_type": _classify_resolution_type(criteria),
    }


__all__ = ["route"]
