#!/usr/bin/env python3
"""Offline evaluator: scores predictions against a resolved event slate.

Reads:
  - events file (any shape supported by `forecasting.dataset_loader`)
  - predictions file: JSON object {task_id: {"p_yes": float, "rationale": "..."}}

Emits:
  - markdown report with Brier / ECE / coverage / parse-failure rate
  - per-domain breakdown
  - price-bucket breakdown (when metadata.market_implied_p_yes is present)

Usage:
    python tools/evaluate_events.py \\
        --events events.json \\
        --predictions predictions.json \\
        --out reports/eval_2026-05-15.md

The predictor SHOULD log raw_event_hash + schema_version_seen + variant
into each prediction so we can join back to specific dataset releases.
This evaluator is tolerant if those fields are absent — they're optional.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.brier import (  # noqa: E402
    brier_score,
    brier_skill_score,
    mean_brier,
)
from evaluation.ece import expected_calibration_error  # noqa: E402
from forecasting.dataset_loader import load  # noqa: E402
from forecasting.schema import (  # noqa: E402
    ForecastTask,
    PredictionOutputError,
    validate_prediction_output,
)


@dataclass
class ScoredRow:
    task_id: str
    p_yes: float
    outcome: int  # 0 or 1 — only set when binary + resolved
    p_market: Optional[float]
    domain: str
    price_bucket: Optional[str]


PRICE_BUCKETS_EDGES = (
    (0.00, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40), (0.40, 0.50),
    (0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.001),
)


def _bucket_label(p_market: float) -> str:
    for lo, hi in PRICE_BUCKETS_EDGES:
        if lo <= p_market < hi:
            return f"{lo:.2f}-{hi:.2f}"
    return "unknown"


def _load_predictions(path: Path) -> Tuple[Dict[str, dict], List[str]]:
    """Load predictions; return (parsed-by-task, parse-errors-list)."""
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        # Allow list-of-{task_id, p_yes, rationale}
        preds = {}
        for row in payload:
            tid = row.get("task_id")
            if tid:
                preds[tid] = row
    elif isinstance(payload, dict):
        preds = payload
    else:
        raise ValueError(f"predictions payload must be list or dict, got {type(payload).__name__}")

    parsed: Dict[str, dict] = {}
    errors: List[str] = []
    for tid, raw in preds.items():
        try:
            po = validate_prediction_output(raw)
            parsed[tid] = {"p_yes": po.p_yes, "rationale": po.rationale}
        except PredictionOutputError as exc:
            errors.append(f"{tid}: {exc}")
    return parsed, errors


def score(
    tasks: List[ForecastTask],
    predictions: Dict[str, dict],
) -> Tuple[List[ScoredRow], List[str], int]:
    """Join tasks <-> predictions, score the resolved binary ones."""
    scored: List[ScoredRow] = []
    missing: List[str] = []
    unresolved = 0

    for t in tasks:
        if t.task_id not in predictions:
            missing.append(t.task_id)
            continue
        if not t.is_binary:
            continue  # multi-outcome scoring not yet implemented
        outcome = t.binary_outcome_int()
        if outcome is None:
            unresolved += 1
            continue

        p_yes = predictions[t.task_id]["p_yes"]
        meta = t.metadata or {}
        p_market = meta.get("market_implied_p_yes")
        try:
            p_market = float(p_market) if p_market is not None else None
        except (TypeError, ValueError):
            p_market = None
        scored.append(
            ScoredRow(
                task_id=t.task_id,
                p_yes=p_yes,
                outcome=outcome,
                p_market=p_market,
                domain=t.category,
                price_bucket=_bucket_label(p_market) if p_market is not None else None,
            )
        )
    return scored, missing, unresolved


# -- Aggregations ---------------------------------------------------------


def overall_metrics(rows: List[ScoredRow]) -> dict:
    if not rows:
        return {"n": 0}
    probs = [r.p_yes for r in rows]
    outcomes = [r.outcome for r in rows]
    bs = mean_brier(probs, outcomes)
    market_probs = [r.p_market for r in rows if r.p_market is not None]
    market_outcomes = [r.outcome for r in rows if r.p_market is not None]
    market_brier = mean_brier(market_probs, market_outcomes) if market_probs else None
    bss = brier_skill_score(bs, market_brier) if market_brier is not None else None
    return {
        "n": len(rows),
        "brier": round(bs, 4),
        "ece": round(expected_calibration_error(probs, outcomes), 4),
        "market_brier": round(market_brier, 4) if market_brier is not None else None,
        "bss_vs_market": round(bss, 4) if bss is not None else None,
    }


def by_key(rows: List[ScoredRow], key_fn) -> Dict[str, dict]:
    grouped: Dict[str, List[ScoredRow]] = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        grouped[k].append(r)
    return {k: overall_metrics(v) for k, v in grouped.items()}


def render(
    overall: dict,
    by_domain: Dict[str, dict],
    by_bucket: Dict[str, dict],
    missing: List[str],
    parse_errors: List[str],
    unresolved: int,
) -> str:
    lines = [
        f"# Offline event evaluation — {datetime.utcnow().isoformat()}Z",
        "",
        "## Overall",
        "",
        f"- n: {overall.get('n', 0)}",
        f"- Brier: {overall.get('brier')}",
        f"- ECE: {overall.get('ece')}",
        f"- Market Brier: {overall.get('market_brier')}",
        f"- BSS vs market: {overall.get('bss_vs_market')}",
        f"- unresolved tasks (skipped): {unresolved}",
        f"- missing predictions: {len(missing)}",
        f"- predictions failed schema: {len(parse_errors)}",
        "",
    ]
    if missing:
        lines.append("Missing predictions (first 20):")
        for tid in missing[:20]:
            lines.append(f"- `{tid}`")
        if len(missing) > 20:
            lines.append(f"- ... ({len(missing) - 20} more)")
        lines.append("")
    if parse_errors:
        lines.append("Parse errors (first 20):")
        for msg in parse_errors[:20]:
            lines.append(f"- {msg}")
        lines.append("")

    lines.append("## By domain")
    lines.append("")
    lines.append("| domain | n | Brier ↓ | ECE ↓ | BSS vs market ↑ |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for domain, m in sorted(by_domain.items(), key=lambda kv: kv[1].get("brier", 1.0)):
        lines.append(
            f"| {domain} | {m.get('n', 0)} | {m.get('brier')} | {m.get('ece')} | {m.get('bss_vs_market')} |"
        )

    if by_bucket:
        lines.append("")
        lines.append("## By price bucket")
        lines.append("")
        lines.append("| bucket | n | Brier ↓ | ECE ↓ |")
        lines.append("| --- | ---: | ---: | ---: |")
        for bucket, m in sorted(by_bucket.items()):
            lines.append(f"| {bucket} | {m.get('n', 0)} | {m.get('brier')} | {m.get('ece')} |")

    return "\n".join(lines) + "\n"


# -- Main -----------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, required=True)
    ap.add_argument("--predictions", type=Path, required=True)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "reports" / f"eval_{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.md")
    args = ap.parse_args()

    result = load(args.events, strict=False)
    preds, parse_errors = _load_predictions(args.predictions)
    rows, missing, unresolved = score(result.tasks, preds)

    overall = overall_metrics(rows)
    by_domain = by_key(rows, lambda r: r.domain)
    by_bucket = by_key(rows, lambda r: r.price_bucket)
    report = render(overall, by_domain, by_bucket, missing, parse_errors, unresolved)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
