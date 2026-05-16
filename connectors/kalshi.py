"""Kalshi connector — OPTIONAL, read-only, research/pastcast/microstructure use.

NOT a trading client. NOT a substitute for the Prophet Arena benchmark
payload. Use this to:

- Fetch resolved-market data for the offline pastcast harness
- Compute price-bucket diagnostics across Kalshi history (the
  empirical basis for the longshot guard in `forecasting.market_blend`)
- Sample microstructure observations (spread, bid/ask, liquidity)
  that inform `executable_edge` calculations

Auth: requires `KALSHI_API_KEY` env var if present; falls back to
public endpoints only. Per the v6 playbook (connector rule), every
record this returns MUST include `source_id`, `retrieved_at`,
`event_id`/`market_id`, `source_type`, quality and staleness scores.

This file is a STUB. Live calls are gated behind
`PROPHET_CONNECTORS_LIVE=1`. The default is OFF.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


SOURCE_ID = "kalshi"
LIVE_FLAG = "PROPHET_CONNECTORS_LIVE"


@dataclass(frozen=True)
class KalshiRecord:
    source_id: str
    url: str
    retrieved_at: str
    market_id: str
    event_id: str
    source_type: str  # "market" | "primary"
    quality_score: float
    staleness_score: float
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    last_price: Optional[float]
    volume: Optional[float]
    status: str  # "open" | "closed" | "settled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_live() -> bool:
    return os.getenv(LIVE_FLAG, "").strip() in ("1", "true", "yes")


def fetch_market(market_id: str) -> Optional[KalshiRecord]:
    """Fetch a single market snapshot. Returns None when live mode is off.

    Live implementation would hit:
        GET https://api.elections.kalshi.com/trade-api/v2/markets/{market_id}

    Per Kalshi public docs as of May 2026. Signature + API-key headers
    required for authenticated endpoints; public ones may not need auth.
    """
    if not is_live():
        return None
    raise NotImplementedError(
        "Live Kalshi fetch not yet wired. Set PROPHET_CONNECTORS_LIVE=1 "
        "AND wire HTTP client + KALSHI_API_KEY env. See "
        "https://trading-api.readme.io/reference/"
    )


def fetch_resolved_markets(limit: int = 100) -> List[KalshiRecord]:
    """Pull a batch of recently-resolved markets for the pastcast
    harness. STUB — returns empty list when live mode is off.
    """
    if not is_live():
        return []
    raise NotImplementedError(
        "Live Kalshi resolved-market fetch not yet wired. See connectors/kalshi.py"
    )


def smoke_check() -> dict:
    """Cheap health check: confirms the module imports and the
    live-flag wiring is sane. No network call."""
    return {
        "source_id": SOURCE_ID,
        "live": is_live(),
        "has_key": bool(os.getenv("KALSHI_API_KEY")),
        "checked_at": _now_iso(),
    }
