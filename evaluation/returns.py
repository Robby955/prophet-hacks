"""Simulated returns + Sharpe + PnL-by-price-bucket.

The Kalshi paper finding (>60% loss on <$0.10 contracts) means we
NEED the price-bucket histogram in our post-event report. Without it
we can't see whether our agent got bitten by the longshot bias.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import List, Sequence

from evaluation.validation import validate_outcome, validate_probability


@dataclass
class Trade:
    market_id: str
    side: str  # "YES" or "NO"
    notional: float
    fill_price: float  # what we paid (0..1) for the YES side
    outcome: int  # 0 or 1
    p_market_at_fill: float
    p_final: float


def trade_payoff(trade: Trade) -> float:
    """Net payoff in $. Assumes binary settlement: $1 per share if right, $0 if wrong.
    `notional` is dollars committed; `fill_price` is the YES probability we
    paid (in $/share). Number of shares = notional / fill_price.
    """
    if trade.notional <= 0:
        raise ValueError("notional must be positive")
    fill_price = validate_probability(trade.fill_price, name="fill_price")
    outcome = validate_outcome(trade.outcome)
    side = trade.side.upper()
    if side == "YES":
        contract_price = fill_price
        wins = outcome == 1
    elif side == "NO":
        contract_price = 1.0 - fill_price
        wins = outcome == 0
    else:
        raise ValueError("side must be YES or NO")
    if contract_price <= 0.0:
        raise ValueError(f"{side} contract price must be positive")
    shares = trade.notional / contract_price
    revenue = shares * 1.0 if wins else 0.0
    return revenue - trade.notional


def simulated_return(trades: Sequence[Trade]) -> float:
    """Sum of net payoffs."""
    return sum(trade_payoff(t) for t in trades)


def per_trade_returns(trades: Sequence[Trade]) -> List[float]:
    """Return / notional for each trade. Useful for Sharpe etc."""
    rets = []
    for t in trades:
        rets.append(trade_payoff(t) / t.notional)
    return rets


def sharpe(trades: Sequence[Trade]) -> float:
    """Annualization-free Sharpe over the trade set.

    Not a real-money Sharpe — just a unitless quality-of-distribution
    signal. Use for variant comparison, not for capital allocation.
    """
    rets = per_trade_returns(trades)
    if len(rets) < 2:
        return 0.0
    m = mean(rets)
    sd = pstdev(rets)
    if sd <= 0:
        return 0.0
    return m / sd


PRICE_BUCKETS = [
    (0.00, 0.10),
    (0.10, 0.20),
    (0.20, 0.30),
    (0.30, 0.40),
    (0.40, 0.50),
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.00),
]


@dataclass
class BucketStats:
    lo: float
    hi: float
    n_trades: int
    total_notional: float
    total_pnl: float
    return_pct: float  # total_pnl / total_notional


def pnl_by_price_bucket(trades: Sequence[Trade]) -> List[BucketStats]:
    """PnL aggregated by market-implied YES probability at fill.

    This is THE Kalshi-style diagnostic. If we're losing money on
    the [0.00, 0.10) bucket the longshot guard is too loose; if we're
    making money there we're either lucky or have real edge.
    """
    for t in trades:
        validate_probability(t.p_market_at_fill, name="p_market_at_fill")

    out = []
    for lo, hi in PRICE_BUCKETS:
        bucket = [
            t
            for t in trades
            if lo <= t.p_market_at_fill < hi
            or (hi == 1.00 and t.p_market_at_fill == 1.00)
        ]
        if not bucket:
            out.append(BucketStats(lo, hi, 0, 0.0, 0.0, 0.0))
            continue
        notional = sum(t.notional for t in bucket)
        pnl = sum(trade_payoff(t) for t in bucket)
        ret = pnl / notional if notional > 0 else 0.0
        out.append(BucketStats(lo, hi, len(bucket), notional, pnl, ret))
    return out
