"""Hard caps and policy invariants. Values live in code, not yaml-only.

config.yaml mirrors these for visibility; the code values here are the
authoritative source for OUR policy. The server's rules in
`ai_prophet_core.ruleset` are imported and asserted against at import
time -- our caps must never be LOOSER than the server's, or we'll silently
submit intents the server will reject.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from ai_prophet_core import ruleset as _server


# Our policy caps. Each must be <= the corresponding server cap.
# Sanity assertions at the bottom of this module enforce the invariant.
EDGE_THRESHOLD: float = 0.08
MAX_MARKETS_ANALYZED_PER_TICK: int = 5
MAX_TRADES_PER_TICK: int = 3
MAX_TRADES_PER_DAY: int = 100               # server: 100 (rolling 24h)
MAX_NOTIONAL_PER_NEW_POSITION: float = 100.0
MAX_OPEN_POSITIONS: int = 30                # server: 30
MAX_NOTIONAL_PER_MARKET: float = 1000.0     # server: 1000
MAX_GROSS_EXPOSURE: float = 10000.0         # server: 10000
TICK_INTERVAL_MIN: int = 15
TICK_SUBMISSION_DEADLINE_SECS: int = 540    # server: 540 (9 min after tick_ts)
STARTING_BANKROLL: float = 10000.0          # server: INITIAL_CASH = 10000

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


def assert_under_daily_trade_count(trades_in_last_24h: int) -> None:
    """Block submitting when the rolling 24h trade count is at or above cap.

    The server rejects fills past `MAX_TRADES_PER_DAY` in a 24h window. We
    don't have direct access to the server's rolling counter, so the caller
    is responsible for passing a best-effort estimate (e.g. count of BUY
    actions in the local trace JSONL over the last 24h).
    """
    if trades_in_last_24h >= MAX_TRADES_PER_DAY:
        raise RiskViolation(
            f"already submitted {trades_in_last_24h} trades in last 24h; "
            f"MAX_TRADES_PER_DAY={MAX_TRADES_PER_DAY}",
        )


def compute_gross_exposure(positions: Iterable) -> float:
    """Sum of `shares * avg_entry_price` across all open positions.

    `positions` is an iterable of PositionData-like objects (`shares`,
    `avg_entry_price` attributes, decimal-string-coercible).
    """
    total = 0.0
    for p in positions:
        try:
            shares = float(p.shares)
            price = float(p.avg_entry_price)
        except (AttributeError, TypeError, ValueError):
            continue
        if shares > 0:
            total += shares * price
    return total


def assert_under_gross_exposure(new_notional: float, current_gross: float) -> None:
    """Block opening a position that would push gross exposure above cap."""
    if current_gross + new_notional > MAX_GROSS_EXPOSURE:
        raise RiskViolation(
            f"gross exposure {current_gross + new_notional:.2f} would exceed "
            f"MAX_GROSS_EXPOSURE {MAX_GROSS_EXPOSURE:.2f}",
        )


def position_size_for_notional(notional: float, price: float) -> int:
    """Integer share count for a target notional at a given price.

    Rounds down so we never exceed the cap due to rounding.
    """
    if price <= 0:
        return 0
    return int(Decimal(str(notional)) / Decimal(str(price)))


# Import-time invariants: our caps must never be LOOSER than the server's.
# A violation here is a programmer error -- ship-stopping. We raise a clear
# AssertionError rather than silently submitting intents the server will
# reject.
assert MAX_TRADES_PER_TICK <= _server.MAX_TRADES_PER_TICK, (
    f"risk.py MAX_TRADES_PER_TICK={MAX_TRADES_PER_TICK} exceeds "
    f"server cap {_server.MAX_TRADES_PER_TICK}"
)
assert MAX_TRADES_PER_DAY <= _server.MAX_TRADES_PER_DAY, (
    f"risk.py MAX_TRADES_PER_DAY={MAX_TRADES_PER_DAY} exceeds "
    f"server cap {_server.MAX_TRADES_PER_DAY}"
)
assert MAX_OPEN_POSITIONS <= _server.MAX_OPEN_POSITIONS, (
    f"risk.py MAX_OPEN_POSITIONS={MAX_OPEN_POSITIONS} exceeds "
    f"server cap {_server.MAX_OPEN_POSITIONS}"
)
assert MAX_NOTIONAL_PER_MARKET <= _server.MAX_NOTIONAL_PER_MARKET, (
    f"risk.py MAX_NOTIONAL_PER_MARKET={MAX_NOTIONAL_PER_MARKET} exceeds "
    f"server cap {_server.MAX_NOTIONAL_PER_MARKET}"
)
assert MAX_GROSS_EXPOSURE <= _server.MAX_GROSS_EXPOSURE, (
    f"risk.py MAX_GROSS_EXPOSURE={MAX_GROSS_EXPOSURE} exceeds "
    f"server cap {_server.MAX_GROSS_EXPOSURE}"
)
assert TICK_SUBMISSION_DEADLINE_SECS <= _server.TICK_SUBMISSION_DEADLINE_SECS, (
    f"risk.py TICK_SUBMISSION_DEADLINE_SECS={TICK_SUBMISSION_DEADLINE_SECS} "
    f"exceeds server cap {_server.TICK_SUBMISSION_DEADLINE_SECS}"
)


__all__ = [
    "EDGE_THRESHOLD",
    "MAX_MARKETS_ANALYZED_PER_TICK",
    "MAX_TRADES_PER_TICK",
    "MAX_TRADES_PER_DAY",
    "MAX_NOTIONAL_PER_NEW_POSITION",
    "MAX_OPEN_POSITIONS",
    "MAX_NOTIONAL_PER_MARKET",
    "MAX_GROSS_EXPOSURE",
    "TICK_INTERVAL_MIN",
    "TICK_SUBMISSION_DEADLINE_SECS",
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
    "assert_under_daily_trade_count",
    "assert_under_gross_exposure",
    "compute_gross_exposure",
    "position_size_for_notional",
]
