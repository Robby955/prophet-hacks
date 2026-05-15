"""JSONL trace writer.

One file per tick: ``trace/{experiment_slug}/{tick_id}.jsonl``.
One JSON record per market decision (BUY / SKIP / etc).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_tick_id(tick_id: str) -> str:
    """Sanitize a tick_id (ISO timestamp) for use as a filename."""
    return tick_id.replace(":", "-").replace("/", "-")


class TraceWriter:
    """Append-only JSONL writer scoped to one experiment + tick."""

    def __init__(self, trace_dir: str, experiment_slug: str, tick_id: str):
        slug = experiment_slug or "unset"
        self.dir = Path(trace_dir) / slug
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{_safe_tick_id(tick_id)}.jsonl"

    def write(self, record: dict[str, Any]) -> None:
        """Append one decision record."""
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


def build_decision_record(
    *,
    tick_id: str,
    market_id: str,
    question: str,
    bid: float,
    ask: float,
    current_position: Optional[dict] = None,
    model_provider: str = "none",
    model: str = "none",
    p_yes: Optional[float] = None,
    probability_bucket: Optional[float] = None,
    implied_market_probability: Optional[float] = None,
    yes_edge: Optional[float] = None,
    no_edge: Optional[float] = None,
    action: str = "SKIP",
    side: Optional[str] = None,
    size: Optional[int] = None,
    notional: Optional[float] = None,
    skip_reason: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    config_hash: Optional[str] = None,
    cost_estimate_usd: float = 0.0,
    evidence_urls: Optional[list[str]] = None,
    notes: str = "",
) -> dict[str, Any]:
    """Construct one decision record. Schema matches the brief verbatim."""
    mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) else None
    return {
        "timestamp": _utcnow_iso(),
        "tick_id": tick_id,
        "market_id": market_id,
        "question": question,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "current_position": current_position,
        "model_provider": model_provider,
        "model": model,
        "p_yes": p_yes,
        "probability_bucket": probability_bucket,
        "implied_market_probability": implied_market_probability,
        "yes_edge": yes_edge,
        "no_edge": no_edge,
        "action": action,
        "side": side,
        "size": size,
        "notional": notional,
        "skip_reason": skip_reason,
        "prompt_hash": prompt_hash,
        "config_hash": config_hash,
        "cost_estimate_usd": cost_estimate_usd,
        "evidence_urls": evidence_urls or [],
        "notes": notes,
    }


__all__ = ["TraceWriter", "build_decision_record"]
