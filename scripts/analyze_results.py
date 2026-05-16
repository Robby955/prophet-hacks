"""Post-event analysis: take live predictions + resolved outcomes, emit a
Brier/ECE/per-category report.

Run me after Prophet Arena resolves events. Reads:
  - predictions JSON (from `GET /predictions` or local file)
  - actuals JSON (`{market_ticker: {winning_outcome: str}}` or
    `{market_ticker: 1.0 | 0.0}` for legacy binary)

Emits:
  - stdout summary table
  - JSON report at reports/analysis_<ts>.json
  - reliability-diagram data ready to be plotted by anything downstream

Usage:
  python scripts/analyze_results.py \\
      --predictions data/live_predictions.json \\
      --actuals data/actuals.json \\
      --out reports/

  # Or pull predictions live from the running server:
  python scripts/analyze_results.py \\
      --predictions-url https://agent.forecastingpath.com/predictions \\
      --token "$DASHBOARD_AUTH_TOKEN" \\
      --actuals data/actuals.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.brier import brier_score, brier_skill_score, murphy_decomposition
from evaluation.ece import expected_calibration_error, reliability_diagram_data


def _load_predictions(path: str | None, url: str | None, token: str | None) -> list[dict]:
    if path:
        with open(path) as f:
            data = json.load(f)
        return data.get("predictions", data) if isinstance(data, dict) else data
    if url:
        import httpx
        params = {"token": token} if token else {}
        r = httpx.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("predictions", [])
    raise SystemExit("provide --predictions PATH or --predictions-url URL")


def _load_actuals(path: str) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def _outcome_index(actual: Any, outcomes: list[str]) -> int | None:
    """Return the index of the winning outcome in `outcomes`, or None."""
    if isinstance(actual, (int, float)):
        return 0 if actual >= 0.5 else 1 if len(outcomes) >= 2 else None
    if isinstance(actual, dict):
        winner = actual.get("winning_outcome") or actual.get("winner") or actual.get("outcome")
        if winner and winner in outcomes:
            return outcomes.index(winner)
    if isinstance(actual, str) and actual in outcomes:
        return outcomes.index(actual)
    return None


def _multi_outcome_brier(probs: list[float], winner_idx: int) -> float:
    """Multi-class Brier: sum of (p_i - 1{i==winner})^2 across all outcomes."""
    return sum((p - (1.0 if i == winner_idx else 0.0)) ** 2 for i, p in enumerate(probs))


def analyze(
    predictions: list[dict],
    actuals: dict[str, Any],
) -> dict[str, Any]:
    # Per-category aggregation
    by_category: dict[str, list[tuple[float, int]]] = defaultdict(list)
    multi_briers: list[tuple[str, float, int]] = []  # (category, brier, n_outcomes)
    binary_probs: list[float] = []
    binary_outcomes: list[int] = []
    unmatched: list[str] = []

    for pred in predictions:
        ticker = pred.get("market_ticker") or pred.get("event_ticker")
        actual = actuals.get(ticker)
        if actual is None:
            unmatched.append(ticker or "?")
            continue
        outcomes = pred.get("outcomes") or []
        probs_list = pred.get("probabilities") or []
        if not outcomes or not probs_list:
            continue
        probs = [p.get("probability", 0.0) for p in probs_list]
        winner_idx = _outcome_index(actual, outcomes)
        if winner_idx is None:
            unmatched.append(ticker or "?")
            continue

        category = pred.get("category", "uncategorized")
        n = len(outcomes)

        if n == 2:
            # Use binary Brier on p_yes (outcomes[0])
            p_yes = probs[0]
            outcome_bit = 1 if winner_idx == 0 else 0
            b = brier_score(p_yes, outcome_bit)
            binary_probs.append(p_yes)
            binary_outcomes.append(outcome_bit)
            by_category[category].append((b, outcome_bit))
        else:
            b = _multi_outcome_brier(probs, winner_idx)
            multi_briers.append((category, b, n))

    # Overall stats
    total_n = len(binary_probs) + len(multi_briers)
    binary_mean_brier = (
        sum(brier_score(p, o) for p, o in zip(binary_probs, binary_outcomes)) / len(binary_probs)
        if binary_probs else None
    )
    multi_mean_brier = (
        sum(b for _, b, _ in multi_briers) / len(multi_briers) if multi_briers else None
    )

    # Random baseline (uniform): binary = 0.25; multi = (n-1)/n^2 * n = (n-1)/n
    binary_baseline = 0.25
    binary_bss = (
        brier_skill_score(binary_mean_brier, binary_baseline)
        if binary_mean_brier is not None else None
    )

    # Calibration (binary only — multi-class ECE needs different formulation)
    ece_value = None
    reliability_bins = []
    murphy = None
    if binary_probs:
        ece_value = expected_calibration_error(binary_probs, binary_outcomes, n_bins=10)
        reliability_bins = [
            {
                "center": b.center,
                "count": b.count,
                "p_mean": b.p_mean,
                "outcome_mean": b.outcome_mean,
            }
            for b in reliability_diagram_data(binary_probs, binary_outcomes, n_bins=10)
        ]
        m = murphy_decomposition(binary_probs, binary_outcomes, n_bins=10)
        murphy = {
            "reliability": m.reliability,
            "resolution": m.resolution,
            "uncertainty": m.uncertainty,
            "brier": m.brier,
        }

    # Per-category breakdown
    category_breakdown = {}
    for cat, items in sorted(by_category.items()):
        ps = [b for b, _ in items]
        category_breakdown[cat] = {
            "n": len(items),
            "mean_brier": sum(ps) / len(ps) if ps else None,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_predictions": total_n,
        "n_binary": len(binary_probs),
        "n_multi_outcome": len(multi_briers),
        "n_unmatched": len(unmatched),
        "unmatched_tickers": unmatched[:10],
        "binary": {
            "mean_brier": binary_mean_brier,
            "baseline_brier": binary_baseline,
            "brier_skill_score": binary_bss,
            "ece": ece_value,
            "murphy_decomposition": murphy,
            "reliability_bins": reliability_bins,
        },
        "multi_outcome": {
            "mean_brier": multi_mean_brier,
            "n": len(multi_briers),
        },
        "by_category": category_breakdown,
    }


def _print_summary(report: dict[str, Any]) -> None:
    print(f"\n=== Analysis report ({report['generated_at']}) ===\n")
    print(f"Predictions analyzed: {report['n_predictions']}"
          f"  (binary: {report['n_binary']}, multi: {report['n_multi_outcome']})")
    if report["n_unmatched"]:
        print(f"  Unmatched (no actual found): {report['n_unmatched']}")

    b = report["binary"]
    if b["mean_brier"] is not None:
        print("\nBinary track:")
        print(f"  Mean Brier:           {b['mean_brier']:.4f}  (lower better; random=0.25)")
        print(f"  Brier Skill Score:    {b['brier_skill_score']:+.4f}  (>0 beats random)")
        print(f"  ECE:                  {b['ece']:.4f}  (lower better)")
        m = b["murphy_decomposition"]
        print(f"  Murphy decomposition: reliability={m['reliability']:.4f}  "
              f"resolution={m['resolution']:.4f}  uncertainty={m['uncertainty']:.4f}")

    if report["multi_outcome"]["mean_brier"] is not None:
        print("\nMulti-outcome track:")
        print(f"  Mean multi-class Brier: {report['multi_outcome']['mean_brier']:.4f}  "
              f"(n={report['multi_outcome']['n']})")

    if report["by_category"]:
        print("\nPer-category breakdown:")
        for cat, stats in report["by_category"].items():
            print(f"  {cat:<20} n={stats['n']:<4}  Brier={stats['mean_brier']:.4f}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--predictions", help="local predictions JSON file")
    p.add_argument("--predictions-url", help="GET /predictions URL")
    p.add_argument("--token", default=os.environ.get("DASHBOARD_AUTH_TOKEN", ""))
    p.add_argument("--actuals", required=True, help="actuals JSON file")
    p.add_argument("--out", default="reports/", help="output directory for JSON report")
    args = p.parse_args()

    predictions = _load_predictions(args.predictions, args.predictions_url, args.token)
    actuals = _load_actuals(args.actuals)

    report = analyze(predictions, actuals)
    _print_summary(report)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"analysis_{stamp}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
