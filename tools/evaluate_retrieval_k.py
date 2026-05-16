#!/usr/bin/env python3
"""Sweep retrieval cap k and report Brier / cost / latency at each k.

OpenForecaster paper finding: accuracy improvements from retrieval
PLATEAU after roughly 5 retrieved chunks. We should verify on our own
pastcast set before committing to the cap. This script is the
verification harness.

For each k in {0, 1, 2, 3, 5, 8}:
  - cap retrieved sources at k in the offline harness
  - re-score each variant
  - report Brier delta, ECE delta, cost-per-forecast estimate

Output: `reports/retrieval_k_sweep.md` with a small table.

Usage:
    PROPHET_OFFLINE_MOCK=1 python tools/evaluate_retrieval_k.py \\
        --dataset offline/sample_tasks.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.brier import brier_skill_score, mean_brier  # noqa: E402
from evaluation.ece import expected_calibration_error  # noqa: E402
from evaluation.no_leakage_check import assert_no_leakage  # noqa: E402


K_VALUES = (0, 1, 2, 3, 5, 8)

# Crude per-source cost model. Sync this with `forecasting.source_gate`.
COST_PER_SOURCE_USD = 0.02


@dataclass
class KResult:
    k: int
    brier: float
    ece: float
    bss_vs_market: float
    est_cost_usd: float


def load_dataset(path: Path) -> List[dict]:
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def evaluate_at_k(dataset: List[dict], k: int) -> KResult:
    """For each event, cap `sources` at k and re-run the
    `market_blend` variant. Mock LLM via `tools.evaluate_offline`
    when PROPHET_OFFLINE_MOCK=1.
    """
    # Import here so the file is usable as a script without circular deps.
    from tools.evaluate_offline import variant_market_blend

    probs = []
    outcomes = []
    market_probs = []
    total_sources = 0

    for event in dataset:
        capped = dict(event)
        capped["sources"] = (event.get("sources") or [])[:k]
        total_sources += len(capped["sources"])
        outcome = int(event["outcome"])
        res = variant_market_blend(capped)
        probs.append(res["p_final"])
        market_probs.append(res["p_market"])
        outcomes.append(outcome)

    brier = mean_brier(probs, outcomes)
    brier_market = mean_brier(market_probs, outcomes)
    bss = brier_skill_score(brier, brier_market)
    ece = expected_calibration_error(probs, outcomes)
    est_cost = total_sources * COST_PER_SOURCE_USD

    return KResult(k=k, brier=brier, ece=ece, bss_vs_market=bss, est_cost_usd=est_cost)


def render(results: List[KResult]) -> str:
    lines = [
        "# Retrieval-k sweep",
        "",
        f"Generated {datetime.utcnow().isoformat()}Z.",
        "Mode: " + ("MOCK" if os.getenv("PROPHET_OFFLINE_MOCK") else "LIVE"),
        "",
        "| k | Brier ↓ | ECE ↓ | BSS vs market ↑ | est cost $ |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        lines.append(
            f"| {r.k} | {r.brier:.4f} | {r.ece:.4f} | {r.bss_vs_market:+.4f} | {r.est_cost_usd:.2f} |"
        )
    lines.extend(
        [
            "",
            "Compare against the OpenForecaster paper's plateau-at-5 result.",
            "If our own plateau is at k=3 or k=8, update `forecasting.source_gate`'s",
            "`source_cap` defaults to match.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "offline" / "sample_tasks.jsonl",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "reports" / "retrieval_k_sweep.md",
    )
    args = ap.parse_args()

    dataset = load_dataset(args.dataset)
    assert_no_leakage(dataset)

    results = [evaluate_at_k(dataset, k) for k in K_VALUES]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
