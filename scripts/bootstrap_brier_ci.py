#!/usr/bin/env python3
"""Paired bootstrap confidence intervals for Brier-score deltas.

The headline Phase 2 comparison is paired: both variants forecast the same
resolved events. Resampling event rows, not individual aggregate scores,
preserves that pairing and estimates uncertainty around the mean delta.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.brier import brier_score  # noqa: E402


@dataclass(frozen=True)
class PairedLoss:
    market_ticker: str
    model_p_yes: float
    baseline_p_yes: float
    outcome: int
    model_loss: float
    baseline_loss: float

    @property
    def improvement(self) -> float:
        """Positive means the model beats the baseline on this event."""
        return self.baseline_loss - self.model_loss


def _prediction_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        rows = data.get("predictions", [])
    else:
        rows = data
    if not isinstance(rows, list):
        raise ValueError("prediction file must be a list or contain a predictions list")
    return rows


def load_prediction_map(path: str | Path) -> dict[str, float]:
    """Load `{market_ticker: p_yes}` from a forecast submission JSON file."""
    payload = json.loads(Path(path).read_text())
    out: dict[str, float] = {}
    for row in _prediction_rows(payload):
        if not isinstance(row, dict):
            continue
        ticker = row.get("market_ticker") or row.get("event_ticker")
        if not ticker or "p_yes" not in row:
            continue
        out[str(ticker)] = float(row["p_yes"])
    return out


def load_actuals(path: str | Path) -> dict[str, int]:
    """Load binary actuals as `{market_ticker: 0|1}`."""
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError("actuals file must be a JSON object")
    actuals: dict[str, int] = {}
    for ticker, raw in payload.items():
        actuals[str(ticker)] = _actual_to_binary(raw)
    return actuals


def _actual_to_binary(raw: Any) -> int:
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        return 1 if float(raw) >= 0.5 else 0
    if isinstance(raw, dict):
        for key in ("resolved_yes", "yes", "value"):
            if key in raw and isinstance(raw[key], (bool, int, float)):
                return 1 if float(raw[key]) >= 0.5 else 0
    raise ValueError(f"unsupported binary actual value: {raw!r}")


def paired_losses(
    model: dict[str, float],
    baseline: dict[str, float],
    actuals: dict[str, int],
) -> list[PairedLoss]:
    """Return per-event paired Brier losses for shared tickers."""
    records: list[PairedLoss] = []
    for ticker in sorted(set(model) & set(baseline) & set(actuals)):
        outcome = actuals[ticker]
        model_p = model[ticker]
        baseline_p = baseline[ticker]
        records.append(
            PairedLoss(
                market_ticker=ticker,
                model_p_yes=model_p,
                baseline_p_yes=baseline_p,
                outcome=outcome,
                model_loss=brier_score(model_p, outcome),
                baseline_loss=brier_score(baseline_p, outcome),
            )
        )
    return records


def summarize(
    records: list[PairedLoss],
    *,
    n_resamples: int,
    seed: int,
    ci_level: float = 0.95,
) -> dict[str, Any]:
    if not records:
        raise ValueError("no paired predictions to compare")
    if n_resamples <= 0:
        raise ValueError("n_resamples must be positive")
    if not 0.0 < ci_level < 1.0:
        raise ValueError("ci_level must be between 0 and 1")

    model_losses = np.asarray([r.model_loss for r in records], dtype=float)
    baseline_losses = np.asarray([r.baseline_loss for r in records], dtype=float)
    improvements = baseline_losses - model_losses

    rng = np.random.default_rng(seed)
    sample_idx = rng.integers(
        low=0,
        high=len(records),
        size=(n_resamples, len(records)),
    )
    sample_means = improvements[sample_idx].mean(axis=1)
    alpha = 1.0 - ci_level
    lo, hi = np.quantile(sample_means, [alpha / 2.0, 1.0 - alpha / 2.0])

    model_mean = float(model_losses.mean())
    baseline_mean = float(baseline_losses.mean())
    mean_improvement = float(improvements.mean())
    relative_reduction = (
        mean_improvement / baseline_mean if baseline_mean > 0.0 else None
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_events": len(records),
        "model_mean_brier": model_mean,
        "baseline_mean_brier": baseline_mean,
        "mean_improvement": mean_improvement,
        "mean_improvement_ci": [float(lo), float(hi)],
        "relative_brier_reduction": (
            float(relative_reduction) if relative_reduction is not None else None
        ),
        "probability_improvement_le_zero": float(np.mean(sample_means <= 0.0)),
        "ci_level": ci_level,
        "n_resamples": n_resamples,
        "seed": seed,
    }


def _print_summary(report: dict[str, Any], model_label: str, baseline_label: str) -> None:
    ci = report["mean_improvement_ci"]
    rel = report["relative_brier_reduction"]
    rel_text = "n/a" if rel is None else f"{rel * 100:.1f}%"
    level = int(report["ci_level"] * 100)
    print(f"Paired events: {report['n_events']}")
    print(f"{model_label} mean Brier:   {report['model_mean_brier']:.6f}")
    print(f"{baseline_label} mean Brier: {report['baseline_mean_brier']:.6f}")
    print(
        "Mean improvement (baseline - model): "
        f"{report['mean_improvement']:.6f} ({rel_text} relative reduction)"
    )
    print(f"{level}% paired-bootstrap CI: [{ci[0]:.6f}, {ci[1]:.6f}]")
    print(
        "Bootstrap Pr(improvement <= 0): "
        f"{report['probability_improvement_le_zero']:.4f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="prediction file for the candidate model")
    parser.add_argument("--baseline", required=True, help="prediction file for the baseline")
    parser.add_argument("--actuals", required=True, help="binary actuals JSON")
    parser.add_argument("--model-label", default="model")
    parser.add_argument("--baseline-label", default="baseline")
    parser.add_argument("--n-resamples", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260516)
    parser.add_argument("--ci-level", type=float, default=0.95)
    parser.add_argument("--out", help="optional JSON output path")
    args = parser.parse_args(argv)

    model = load_prediction_map(args.model)
    baseline = load_prediction_map(args.baseline)
    actuals = load_actuals(args.actuals)
    records = paired_losses(model, baseline, actuals)
    report = summarize(
        records,
        n_resamples=args.n_resamples,
        seed=args.seed,
        ci_level=args.ci_level,
    )
    report.update({
        "model_path": args.model,
        "baseline_path": args.baseline,
        "actuals_path": args.actuals,
        "model_label": args.model_label,
        "baseline_label": args.baseline_label,
        "n_model_predictions": len(model),
        "n_baseline_predictions": len(baseline),
        "n_actuals": len(actuals),
        "per_event": [asdict(r) | {"improvement": r.improvement} for r in records],
    })

    _print_summary(report, args.model_label, args.baseline_label)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, allow_nan=False))
        print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
