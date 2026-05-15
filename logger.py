"""JSONL trace writer.

One file per tick: ``trace/{experiment_slug}/{tick_id}.jsonl``.
One JSON record per market decision (BUY / SKIP / etc).

The base 21-field schema is locked. v2 added a handful of optional fields
(domain, evidence_sources, decomposition_json, p_market, p_model_raw,
p_model_shrunk, p_final, disagreement_stdev, alpha_vs_market,
skip_reason_detailed). Old rows remain valid because every new field
defaults to None or an empty list.
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
    # ----- v2 optional fields (default to None / empty so old rows stay valid)
    domain: Optional[str] = None,
    evidence_sources: Optional[list[dict]] = None,
    decomposition_json: Optional[dict] = None,
    p_market: Optional[float] = None,
    p_model_raw: Optional[float] = None,
    p_model_shrunk: Optional[float] = None,
    p_final: Optional[float] = None,
    disagreement_stdev: Optional[float] = None,
    alpha_vs_market: Optional[float] = None,
    skip_reason_detailed: Optional[str] = None,
) -> dict[str, Any]:
    """Construct one decision record. Schema matches the brief verbatim,
    plus optional v2 fields for the calibrated-ensemble pipeline."""
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
        # v2 optional fields
        "domain": domain,
        "evidence_sources": evidence_sources or [],
        "decomposition_json": decomposition_json,
        "p_market": p_market,
        "p_model_raw": p_model_raw,
        "p_model_shrunk": p_model_shrunk,
        "p_final": p_final,
        "disagreement_stdev": disagreement_stdev,
        "alpha_vs_market": alpha_vs_market,
        "skip_reason_detailed": skip_reason_detailed,
    }


def append_experiment_row(
    *,
    log_path: str = "docs/EXPERIMENT_LOG.md",
    slug: str,
    variant: str,
    config_hash: str,
    tick_count: int,
    outcome: str,
    notes: str = "",
) -> None:
    """Append one row to docs/EXPERIMENT_LOG.md after a tick completes.

    Safe to call repeatedly; pipe-character columns are escaped so
    free-form ``notes`` cannot break the table.
    """
    def _esc(s: str) -> str:
        return s.replace("|", "\\|").replace("\n", " ").strip()

    row = (
        f"| {_utcnow_iso()} | {_esc(slug)} | {_esc(variant)} | "
        f"{_esc(config_hash)} | {tick_count} | {_esc(outcome)} | "
        f"{_esc(notes)} |\n"
    )
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        header = (
            "# Experiment log\n\n"
            "| timestamp | slug | variant | config_hash | tick_count | outcome | notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
        )
        p.write_text(header, encoding="utf-8")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(row)


__all__ = ["TraceWriter", "build_decision_record", "append_experiment_row"]
