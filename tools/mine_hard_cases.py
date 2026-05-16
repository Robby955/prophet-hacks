#!/usr/bin/env python3
"""Mine "hard examples" from a JSONL trace for prompt / calibrator
/ stacker improvement and Opus-escalation tuning.

Per the OpenForecaster lesson: GRPO gains are largest when training
on hard examples, because hard cases maintain nonzero reward variance
longer and give post-training more learnable signal. We don't fine-
tune, but the same principle applies to:
- prompt refinement (which prompt fixes the failure mode?)
- calibrator training (which feature predicts the residual?)
- Opus escalation policy (where does Opus actually help?)
- longshot guard tuning (which guard threshold avoids the most bad trades?)

Hard-case criteria (the union, not the intersection):
  - market vs model disagreement > 0.15
  - GPT vs Opus disagreement > 0.12 (when both fired)
  - retrieval sources conflict (source_disagreement > 0.30)
  - longshot market (p_market < 0.10) where model wanted to lift > 0.05
  - market near threshold but uncertainty aggregate > 0.30
  - prior variant made a high-confidence miss
    (|p_final - 0.5| > 0.30 AND wrong direction at resolution)

Output: `reports/hard_cases.md` — a markdown table the next prompt /
calibrator iteration can inspect by hand.

Usage:
    python tools/mine_hard_cases.py --trace traces/run-*.jsonl \\
        --out reports/hard_cases.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class TraceRow:
    raw: dict
    market_id: str
    p_market: float
    p_final: float
    p_model_raw: Optional[float]
    p_gpt: Optional[float]
    p_opus: Optional[float]
    source_disagreement: float
    uncertainty_aggregate: float
    outcome: Optional[int]  # None = unresolved
    domain: str
    action: str
    # Pre-computed criteria scores
    flags: List[str] = field(default_factory=list)


def _maybe_float(d: dict, key: str) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_row(d: dict) -> Optional[TraceRow]:
    market_id = d.get("market_id")
    if not market_id:
        return None
    p_market = _maybe_float(d, "p_market")
    p_final = _maybe_float(d, "p_final")
    if p_market is None or p_final is None:
        return None
    return TraceRow(
        raw=d,
        market_id=market_id,
        p_market=p_market,
        p_final=p_final,
        p_model_raw=_maybe_float(d, "p_model_raw"),
        p_gpt=_maybe_float(d, "p_gpt") or _maybe_float(d, "p_triage"),
        p_opus=_maybe_float(d, "p_opus") or _maybe_float(d, "p_strong"),
        source_disagreement=_maybe_float(d, "source_disagreement") or 0.0,
        uncertainty_aggregate=_maybe_float(d, "uncertainty_aggregate") or 0.0,
        outcome=d.get("outcome") if d.get("outcome") in (0, 1) else None,
        domain=d.get("domain", "other"),
        action=d.get("action") or d.get("skip_reason_detailed") or "unknown",
    )


def is_hard(row: TraceRow,
            edge_threshold: float = 0.08,
            longshot_p: float = 0.10,
            longshot_lift: float = 0.05) -> List[str]:
    flags: List[str] = []

    # 1. market vs model disagreement
    if row.p_model_raw is not None and abs(row.p_model_raw - row.p_market) > 0.15:
        flags.append("market_model_disagree>0.15")

    # 2. GPT vs Opus disagreement
    if row.p_gpt is not None and row.p_opus is not None:
        if abs(row.p_gpt - row.p_opus) > 0.12:
            flags.append("gpt_opus_disagree>0.12")

    # 3. retrieval sources conflict
    if row.source_disagreement > 0.30:
        flags.append("sources_conflict>0.30")

    # 4. longshot market where model wanted to lift
    if row.p_market < longshot_p:
        lift = (row.p_model_raw or row.p_final) - row.p_market
        if lift > longshot_lift:
            flags.append(f"longshot_lift>{longshot_lift}")

    # 5. near threshold but uncertain
    near_threshold = abs(row.p_final - 0.5) < edge_threshold * 1.5
    if near_threshold and row.uncertainty_aggregate > 0.30:
        flags.append("near_threshold_uncertain")

    # 6. high-confidence miss (only if resolved)
    if row.outcome is not None:
        conf = abs(row.p_final - 0.5)
        wrong_direction = (
            (row.outcome == 1 and row.p_final < 0.5)
            or (row.outcome == 0 and row.p_final > 0.5)
        )
        if conf > 0.30 and wrong_direction:
            flags.append("high_conf_miss")

    return flags


def load_jsonl(paths: Sequence[Path]) -> Iterable[dict]:
    for p in paths:
        if not p.exists():
            continue
        with p.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def render(rows: List[TraceRow]) -> str:
    lines = [
        "# Hard cases mined from trace",
        "",
        f"Total flagged: **{len(rows)}** rows. Sort: most flags first, then |p_final - p_market|.",
        "",
        "| market_id | domain | p_market | p_final | outcome | action | flags |",
        "| --- | --- | ---: | ---: | :---: | --- | --- |",
    ]
    rows_sorted = sorted(
        rows,
        key=lambda r: (-len(r.flags), -abs(r.p_final - r.p_market)),
    )
    for r in rows_sorted[:200]:
        outcome = "—" if r.outcome is None else str(r.outcome)
        flag_str = ", ".join(r.flags) if r.flags else ""
        lines.append(
            f"| `{r.market_id}` | {r.domain} | {r.p_market:.3f} | {r.p_final:.3f} | "
            f"{outcome} | {r.action} | {flag_str} |"
        )
    if len(rows_sorted) > 200:
        lines.append("")
        lines.append(f"_({len(rows_sorted) - 200} additional rows omitted from this table.)_")

    # Aggregate flag counts
    from collections import Counter
    flag_counter: Counter = Counter()
    for r in rows_sorted:
        for f in r.flags:
            flag_counter[f] += 1
    lines.append("")
    lines.append("## Flag distribution")
    lines.append("")
    lines.append("| flag | count |")
    lines.append("| --- | ---: |")
    for f, n in flag_counter.most_common():
        lines.append(f"| `{f}` | {n} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", type=Path, nargs="+", required=True,
                    help="One or more JSONL trace files")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "hard_cases.md")
    ap.add_argument("--edge-threshold", type=float, default=0.08)
    ap.add_argument("--longshot-p", type=float, default=0.10)
    ap.add_argument("--longshot-lift", type=float, default=0.05)
    args = ap.parse_args()

    hard_rows: List[TraceRow] = []
    for d in load_jsonl(args.trace):
        row = parse_row(d)
        if row is None:
            continue
        flags = is_hard(
            row,
            edge_threshold=args.edge_threshold,
            longshot_p=args.longshot_p,
            longshot_lift=args.longshot_lift,
        )
        if flags:
            row.flags = flags
            hard_rows.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(hard_rows))
    print(f"Wrote {args.out} ({len(hard_rows)} hard cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
