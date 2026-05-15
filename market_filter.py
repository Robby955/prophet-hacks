"""Market eligibility filters.

Markets are dropped if any of the following are true:
- bad quote (bid >= ask, or bid/ask outside [0, 1])
- stale data (quote ts older than STALE_AFTER_MIN minutes)
- conflicting positions on the same market_id
- unclear resolution (resolution_time missing or already past)
- spread is so wide that the available edge cannot exceed EDGE_THRESHOLD

The skeleton is conservative: when in doubt, drop. We can loosen later.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from risk import EDGE_THRESHOLD


STALE_AFTER_MIN: int = 30
MIN_VOLUME_24H: float = 0.0
MIN_SPREAD_HEADROOM: float = 0.02


@dataclass
class FilterResult:
    eligible: bool
    reason: str | None = None


def _to_float(s) -> float:
    if s is None:
        return 0.0
    return float(s)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def check_quote_sanity(market) -> FilterResult:
    bid = _to_float(market.quote.best_bid)
    ask = _to_float(market.quote.best_ask)
    if not (0.0 <= bid <= 1.0) or not (0.0 <= ask <= 1.0):
        return FilterResult(False, "quote out of [0, 1]")
    if bid >= ask:
        return FilterResult(False, f"bid {bid} >= ask {ask}")
    return FilterResult(True)


def check_quote_freshness(market, now: datetime | None = None) -> FilterResult:
    now = now or _now_utc()
    ts = _aware(market.quote.ts)
    age = now - ts
    if age > timedelta(minutes=STALE_AFTER_MIN):
        return FilterResult(False, f"quote stale by {age}")
    return FilterResult(True)


def check_resolution_time(market, now: datetime | None = None) -> FilterResult:
    now = now or _now_utc()
    res = _aware(market.resolution_time)
    if res <= now:
        return FilterResult(False, "resolution_time already past")
    return FilterResult(True)


def check_volume(market) -> FilterResult:
    vol = float(market.quote.volume_24h or 0.0)
    if vol < MIN_VOLUME_24H:
        return FilterResult(False, f"volume_24h {vol} below MIN_VOLUME_24H")
    return FilterResult(True)


def check_no_conflicting_position(market, positions: Iterable) -> FilterResult:
    sides_held = {pos.side.upper() for pos in positions if pos.market_id == market.market_id}
    if "YES" in sides_held and "NO" in sides_held:
        return FilterResult(False, "already holding both YES and NO; will not add")
    return FilterResult(True)


def check_edge_headroom(market) -> FilterResult:
    """If both yes_ask and no_ask are far enough from {0, 1} that even a
    perfect forecast cannot exceed EDGE_THRESHOLD, drop the market.
    """
    yes_ask = _to_float(market.quote.best_ask)
    no_ask = 1.0 - _to_float(market.quote.best_bid)
    best_possible_yes_edge = 1.0 - yes_ask
    best_possible_no_edge = 1.0 - no_ask
    headroom = max(best_possible_yes_edge, best_possible_no_edge)
    if headroom < EDGE_THRESHOLD + MIN_SPREAD_HEADROOM:
        return FilterResult(
            False,
            f"max possible edge {headroom:.3f} below threshold "
            f"{EDGE_THRESHOLD + MIN_SPREAD_HEADROOM:.3f}",
        )
    return FilterResult(True)


def filter_market(market, positions: Iterable, now: datetime | None = None) -> FilterResult:
    """Apply all checks. First failure wins."""
    for check in (
        check_quote_sanity(market),
        check_quote_freshness(market, now=now),
        check_resolution_time(market, now=now),
        check_volume(market),
        check_no_conflicting_position(market, positions),
        check_edge_headroom(market),
    ):
        if not check.eligible:
            return check
    return FilterResult(True)


def filter_candidates(markets: Iterable, positions: Iterable, now: datetime | None = None) -> list:
    """Return only eligible markets, preserving input order."""
    out = []
    pos_list = list(positions)
    for m in markets:
        if filter_market(m, pos_list, now=now).eligible:
            out.append(m)
    return out


__all__ = [
    "STALE_AFTER_MIN",
    "MIN_VOLUME_24H",
    "MIN_SPREAD_HEADROOM",
    "FilterResult",
    "check_quote_sanity",
    "check_quote_freshness",
    "check_resolution_time",
    "check_volume",
    "check_no_conflicting_position",
    "check_edge_headroom",
    "filter_market",
    "filter_candidates",
]
