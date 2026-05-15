"""Hard caps and policy invariants. Values live in code, not yaml-only.

config.yaml mirrors these for visibility; the code values here are the
authoritative source. If they disagree, the code values win.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable


EDGE_THRESHOLD: float = 0.08
MAX_MARKETS_ANALYZED_PER_TICK: int = 5
MAX_TRADES_PER_TICK: int = 3
MAX_NOTIONAL_PER_NEW_POSITION: float = 100.0
MAX_OPEN_POSITIONS: int = 30
MAX_NOTIONAL_PER_MARKET: float = 1000.0
TICK_INTERVAL_MIN: int = 15
STARTING_BANKROLL: float = 10000.0

P_YES_MIN: float = 0.01
P_YES_MAX: float = 0.99

MAX_MODEL_CALLS_PER_TICK: int = 8


class RiskViolation(Exception):
    """Raised when a policy invariant would be violated."""


def clamp_p_yes(p: float) -> float:
    """Clamp a forecast into the scoring-safe range."""
    if p < P_YES_MIN:
        return P_YES_MIN
    if p > P_YES_MAX:
        return P_YES_MAX
    return p


def assert_no_conflicting_position(market_id: str, side: str, existing_positions: Iterable) -> None:
    """Block holding both YES and NO on the same market.

    ``existing_positions`` is an iterable of objects with ``market_id`` and
    ``side`` attributes (PositionData from ai-prophet-core, or any duck-typed
    equivalent).
    """
    side_norm = side.upper()
    opposite = "NO" if side_norm == "YES" else "YES"
    for pos in existing_positions:
        if pos.market_id == market_id and pos.side.upper() == opposite:
            raise RiskViolation(
                f"would hold both YES and NO on market {market_id}; "
                f"existing side={pos.side}, attempted side={side}",
            )


def assert_under_notional_cap(notional: float, current_market_notional: float = 0.0) -> None:
    """Block new positions above the per-position cap or per-market cap."""
    if notional > MAX_NOTIONAL_PER_NEW_POSITION:
        raise RiskViolation(
            f"notional {notional:.2f} exceeds MAX_NOTIONAL_PER_NEW_POSITION "
            f"{MAX_NOTIONAL_PER_NEW_POSITION:.2f}",
        )
    if current_market_notional + notional > MAX_NOTIONAL_PER_MARKET:
        raise RiskViolation(
            f"market notional {current_market_notional + notional:.2f} would "
            f"exceed MAX_NOTIONAL_PER_MARKET {MAX_NOTIONAL_PER_MARKET:.2f}",
        )


def assert_under_position_count(current_open_positions: int) -> None:
    """Block opening a new position when at the open-position cap."""
    if current_open_positions >= MAX_OPEN_POSITIONS:
        raise RiskViolation(
            f"already at MAX_OPEN_POSITIONS {MAX_OPEN_POSITIONS}; cannot open more",
        )


def assert_under_trades_per_tick(trades_this_tick: int) -> None:
    if trades_this_tick >= MAX_TRADES_PER_TICK:
        raise RiskViolation(
            f"already submitted {trades_this_tick} trades this tick; "
            f"MAX_TRADES_PER_TICK={MAX_TRADES_PER_TICK}",
        )


def position_size_for_notional(notional: float, price: float) -> int:
    """Integer share count for a target notional at a given price.

    Rounds down so we never exceed the cap due to rounding.
    """
    if price <= 0:
        return 0
    return int(Decimal(str(notional)) / Decimal(str(price)))


__all__ = [
    "EDGE_THRESHOLD",
    "MAX_MARKETS_ANALYZED_PER_TICK",
    "MAX_TRADES_PER_TICK",
    "MAX_NOTIONAL_PER_NEW_POSITION",
    "MAX_OPEN_POSITIONS",
    "MAX_NOTIONAL_PER_MARKET",
    "TICK_INTERVAL_MIN",
    "STARTING_BANKROLL",
    "P_YES_MIN",
    "P_YES_MAX",
    "MAX_MODEL_CALLS_PER_TICK",
    "RiskViolation",
    "clamp_p_yes",
    "assert_no_conflicting_position",
    "assert_under_notional_cap",
    "assert_under_position_count",
    "assert_under_trades_per_tick",
    "position_size_for_notional",
]
