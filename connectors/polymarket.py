"""Polymarket connector — OPTIONAL, read-only, cross-market consensus.

Use this for: external-prior comparison ("what does Polymarket think
about the same event?") and cross-market consensus diagnostics. NOT a
trading client. NOT used for live evaluation against Prophet Arena.

The point is to log cross-venue disagreement as an additional
uncertainty signal: if Polymarket and the in-benchmark market disagree
sharply on a recognizably-same event, that's a flag the model should
shrink harder.

This file is a STUB. Live calls are gated behind
`PROPHET_CONNECTORS_LIVE=1`. The default is OFF.

Policy reminders:
- No private keys or wallet credentials in this repo, ever.
- No live-money trading via this connector.
- Per playbook: domain mismatch and trading distractions are the
  main failure modes for using Polymarket data naively.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


SOURCE_ID = "polymarket"
LIVE_FLAG = "PROPHET_CONNECTORS_LIVE"


@dataclass(frozen=True)
class PolymarketRecord:
    source_id: str
    url: str
    retrieved_at: str
    market_slug: str
    related_event_id: str  # the Prophet Arena event we mapped this to
    source_type: str  # "market"
    quality_score: float
    staleness_score: float
    yes_price: Optional[float]
    no_price: Optional[float]
    volume_24h: Optional[float]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_live() -> bool:
    return os.getenv(LIVE_FLAG, "").strip() in ("1", "true", "yes")


def fetch_market(slug: str) -> Optional[PolymarketRecord]:
    """Fetch a single Polymarket market by slug. STUB when live mode is off.

    Live implementation would hit the public Polymarket CLOB or
    data-api endpoint. Public market-data calls do not require auth.
    """
    if not is_live():
        return None
    raise NotImplementedError(
        "Live Polymarket fetch not yet wired. Set PROPHET_CONNECTORS_LIVE=1 "
        "AND wire HTTP client. See https://docs.polymarket.com/"
    )


def map_event_to_polymarket(event_text: str) -> Optional[str]:
    """Heuristic event-text → Polymarket slug mapper. STUB.

    A real implementation could do fuzzy matching on Polymarket's
    public market index. For now, returns None so the agent treats
    Polymarket as "no signal" and falls back to the Prophet Arena
    payload alone.
    """
    return None


def smoke_check() -> dict:
    return {
        "source_id": SOURCE_ID,
        "live": is_live(),
        "checked_at": _now_iso(),
    }
