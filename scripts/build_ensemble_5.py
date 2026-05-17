#!/usr/bin/env python3
"""Ensemble-of-5: average the variance reruns + canonical to reduce noise.

We already have 6 production-equivalent prediction runs on disk:
- data/predictions/multi_outcome_retrieval.json (canonical)
- data/predictions/variance_run_1..5.json (5 fresh reruns from 02:30 CT)

Free experiment: for each event, ensemble the per-outcome probabilities
(mean and median) across all 6 runs. Compute single-binary and multi-
class Brier on the ensembled predictions. If the variance noise floor
is sigma=0.0009 per run, ensembling 6 runs should lower it to ~0.00037
(sqrt(6) reduction). The interesting question is whether the mean
Brier moves measurably, not just the variance.

No API spend. ~10 seconds runtime. Writes
data/predictions/ensemble_5.json + ensemble_5_summary.json and
appends to summary.html via a new section.
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"

RUN_FILES = [
    PRED / "multi_outcome_retrieval.json",
    PRED / "variance_run_1.json",
    PRED / "variance_run_2.json",
    PRED / "variance_run_3.json",
    PRED / "variance_run_4.json",
    PRED / "variance_run_5.json",
]


def load_run(path: Path) -> dict[str, dict]:
    d = json.loads(path.read_text())
    rows = d.get("predictions", d)
    return {r["market_ticker"]: r for r in rows}


def main() -> int:
    actuals = {k: float(v) for k, v in json.load(open(DATA / "actuals.json")).items()}
    events = {e["market_ticker"]: e for e in json.load(open(DATA / "resolved.json"))}

    runs = [load_run(p) for p in RUN_FILES]
    tickers = sorted(set.intersection(*[set(r.keys()) for r in runs]))

    out_rows = []
    per_run_losses: dict[str, list[float]] = defaultdict(list)
    for t in tickers:
        a = actuals.get(t)
        if a is None:
            continue
        per_run_p = [float(r[t]["p_yes"]) for r in runs]
        for i, p in enumerate(per_run_p):
            per_run_losses[f"run_{i}"].append((p - a) ** 2)

        # Ensemble outcomes-array as well so multi-class survives.
        # Collect all unique outcome labels seen across runs; average each
        # probability across runs that have it. If a run misses an outcome,
        # treat as 0 mass (it gets distributed away in renormalize).
        all_probs: dict[str, list[float]] = defaultdict(list)
        for r in runs:
            for entry in r[t].get("probabilities", []):
                all_probs[entry["market"]].append(float(entry["probability"]))
        mean_probs = {k: sum(vs) / len(runs) for k, vs in all_probs.items()}
        # Renormalize to sum to ~1 (the per-run probs already pass through
        # apply_longshot_guard which normalizes; the per-run sum is ~1).
        total = sum(mean_probs.values()) or 1.0
        mean_probs = {k: v / total for k, v in mean_probs.items()}

        # The canonical single-binary metric uses p_yes on outcomes[0].
        # outcomes[0] is event-defined; pull from the resolved event.
        ev = events.get(t, {})
        outcomes = ev.get("outcomes") or []
        outcome0 = outcomes[0] if outcomes else None
        if outcome0 is None:
            continue
        p_yes_mean = float(st.mean(per_run_p))
        p_yes_median = float(st.median(per_run_p))

        out_rows.append({
            "market_ticker": t,
            "outcomes": outcomes,
            "actual": int(a),
            "per_run_p_yes": per_run_p,
            "p_yes_mean": p_yes_mean,
            "p_yes_median": p_yes_median,
            "p_yes_std": float(st.stdev(per_run_p)) if len(per_run_p) > 1 else 0.0,
            "mean_probs": mean_probs,
            "brier_mean_ensemble": (p_yes_mean - a) ** 2,
            "brier_median_ensemble": (p_yes_median - a) ** 2,
        })

    n = len(out_rows)
    if not n:
        print("no usable events")
        return 1

    # Per-run individual means (for reference)
    per_run_mean_brier = {k: sum(v) / len(v) for k, v in per_run_losses.items()}
    ensemble_mean_brier_mean = sum(r["brier_mean_ensemble"] for r in out_rows) / n
    ensemble_median_brier = sum(r["brier_median_ensemble"] for r in out_rows) / n

    summary = {
        "n_events": n,
        "n_runs_ensembled": len(runs),
        "per_run_mean_brier": per_run_mean_brier,
        "single_run_mean": float(st.mean(per_run_mean_brier.values())),
        "single_run_std": float(st.stdev(per_run_mean_brier.values())),
        "ensemble_mean_Brier_arithmetic_mean": ensemble_mean_brier_mean,
        "ensemble_mean_Brier_median": ensemble_median_brier,
        "delta_ensemble_minus_grand_mean": ensemble_mean_brier_mean - float(st.mean(per_run_mean_brier.values())),
    }
    (PRED / "ensemble_5_summary.json").write_text(json.dumps(summary, indent=2))
    (PRED / "ensemble_5.json").write_text(json.dumps({
        "n_runs_ensembled": len(runs),
        "predictions": out_rows,
    }, indent=2))

    print(f"=== Ensemble of {len(runs)} production reruns ===")
    print(f"events: {n}")
    print()
    print(f"{'run':<10} {'mean Brier':>12}")
    for k, v in per_run_mean_brier.items():
        print(f"{k:<10} {v:>12.5f}")
    print(f"{'GRAND MEAN':<10} {summary['single_run_mean']:>12.5f}   (sigma {summary['single_run_std']:.5f})")
    print()
    print(f"Ensemble (arithmetic mean of p_yes across runs): {ensemble_mean_brier_mean:.5f}")
    print(f"Ensemble (median of p_yes across runs):          {ensemble_median_brier:.5f}")
    print(f"Delta (ensemble - grand mean): {summary['delta_ensemble_minus_grand_mean']:+.5f}")
    print()
    # Per-event tail: which events did the ensemble change the most?
    tail = sorted(out_rows, key=lambda r: -abs(r["p_yes_std"]))[:5]
    print("Top 5 most-noisy events across reruns:")
    for r in tail:
        print(f"  {r['market_ticker']:<35} stdev={r['p_yes_std']:.4f} per_run_p={[f'{p:.2f}' for p in r['per_run_p_yes']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
