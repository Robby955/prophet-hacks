#!/usr/bin/env python3
"""Check paired-bootstrap CI stability across independent RNG seeds."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from bootstrap_brier_ci import (  # noqa: E402
    load_actuals,
    load_prediction_map,
    paired_losses,
    summarize,
)


DEFAULT_MODEL = REPO / "data" / "predictions" / "multi_outcome_retrieval.json"
DEFAULT_BASELINE = (
    REPO / "data" / "predictions" / "multi_outcome_retrieval.phase1_sonnet.json"
)
DEFAULT_ACTUALS = REPO / "data" / "actuals.json"


def _parse_seeds(raw: str) -> list[int]:
    seeds = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    return seeds


def _intervals_overlap(reports: list[dict[str, Any]]) -> bool:
    lows = [float(r["mean_improvement_ci"][0]) for r in reports]
    highs = [float(r["mean_improvement_ci"][1]) for r in reports]
    return max(lows) <= min(highs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--actuals", default=str(DEFAULT_ACTUALS))
    parser.add_argument("--seeds", type=_parse_seeds, default=[20260516, 20260517, 20260518])
    parser.add_argument("--n-resamples", type=int, default=50_000)
    parser.add_argument("--ci-level", type=float, default=0.95)
    parser.add_argument("--tolerance", type=float, default=0.001)
    parser.add_argument("--out", help="optional JSON output path")
    args = parser.parse_args(argv)

    model = load_prediction_map(args.model)
    baseline = load_prediction_map(args.baseline)
    actuals = load_actuals(args.actuals)
    records = paired_losses(model, baseline, actuals)

    reports: list[dict[str, Any]] = []
    for seed in args.seeds:
        report = summarize(
            records,
            n_resamples=args.n_resamples,
            seed=seed,
            ci_level=args.ci_level,
        )
        reports.append(report)

    lows = [float(r["mean_improvement_ci"][0]) for r in reports]
    highs = [float(r["mean_improvement_ci"][1]) for r in reports]
    max_low_spread = max(lows) - min(lows)
    max_high_spread = max(highs) - min(highs)
    stable = (
        _intervals_overlap(reports)
        and max_low_spread <= args.tolerance
        and max_high_spread <= args.tolerance
    )

    print("seed,n_events,mean_improvement,ci_low,ci_high,pr_improvement_le_zero")
    for report in reports:
        lo, hi = report["mean_improvement_ci"]
        print(
            f"{report['seed']},{report['n_events']},"
            f"{report['mean_improvement']:.6f},{lo:.6f},{hi:.6f},"
            f"{report['probability_improvement_le_zero']:.6f}"
        )
    print(
        "stable="
        f"{str(stable).lower()} "
        f"low_endpoint_spread={max_low_spread:.6f} "
        f"high_endpoint_spread={max_high_spread:.6f} "
        f"tolerance={args.tolerance:.6f}"
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "stable": stable,
            "low_endpoint_spread": max_low_spread,
            "high_endpoint_spread": max_high_spread,
            "tolerance": args.tolerance,
            "reports": reports,
        }, indent=2, allow_nan=False))
        print(f"wrote {out_path}")

    return 0 if stable else 1


if __name__ == "__main__":
    raise SystemExit(main())
