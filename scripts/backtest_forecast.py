#!/usr/bin/env python3
"""Backtest forecasting-track variants against a resolved-events dataset.

Runs each named variant from `forecast_track.py` over every event in the
input file, writes one predictions JSON per variant in the format
`prophet forecast evaluate` expects, then runs the evaluator and prints
a summary table.

Usage:
    python scripts/backtest_forecast.py \
        --events data/resolved.json \
        --actuals data/actuals.json \
        --out-dir data/predictions \
        --variants uniform_prior,single_llm

`uniform_prior` is free. `single_llm` calls Anthropic Sonnet 4.6 once per
event (~$0.005/event * 26 events ≈ $0.13 against the sample-resolved set).
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forecast_track  # noqa: E402


log = logging.getLogger("backtest")

VARIANTS = {
    "uniform_prior": forecast_track.predict_uniform_prior,
    "single_llm": forecast_track.predict_single_llm,
    "opus_47": forecast_track.predict_opus_47,
    "opus_46": forecast_track.predict_opus_46,
    "gpt55": forecast_track.predict_gpt55,
    "gpt52": forecast_track.predict_gpt52,
    "ensemble_logit": forecast_track.predict_ensemble_logit,
    "ensemble_leaderboard": forecast_track.predict_ensemble_leaderboard,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _predict_one(name: str, fn, event: dict) -> dict:
    """Predict a single event; never raises — errors become 0.5 fallbacks."""
    ticker = event.get("market_ticker") or event.get("event_ticker")
    try:
        result = fn(event)
    except Exception as ex:
        log.warning("[%s] event %s raised: %s", name, ticker, ex)
        result = {"p_yes": 0.5, "rationale": f"error: {ex}"}
    return {
        "market_ticker": ticker,
        "p_yes": float(result["p_yes"]),
        "rationale": str(result.get("rationale", ""))[:300],
    }


def run_variant(
    name: str, events: list[dict], *, max_workers: int = 5,
) -> list[dict]:
    """Run one variant over every event in parallel; preserves event order.

    `max_workers` caps concurrent provider API calls. Default 5 is well under
    most provider RPM limits while still being ~5x faster than serial. Bump
    to 10-20 if the account tier allows.
    """
    fn = VARIANTS[name]
    start = time.monotonic()
    preds: list[dict | None] = [None] * len(events)
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_idx = {
            ex.submit(_predict_one, name, fn, e): i
            for i, e in enumerate(events)
        }
        for fut in as_completed(future_to_idx):
            i = future_to_idx[fut]
            preds[i] = fut.result()
            completed += 1
            if completed % 5 == 0 or completed == len(events):
                elapsed = time.monotonic() - start
                log.info(
                    "[%s] %d/%d (%.1fs, %d workers)",
                    name, completed, len(events), elapsed, max_workers,
                )
    return preds  # type: ignore[return-value]


def write_submission(predictions: list[dict], out_path: Path) -> None:
    submission = {
        "timestamp": _now_iso(),
        "predictions": predictions,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(submission, indent=2))


def evaluate(submission_path: Path, actuals_path: Path) -> dict:
    """Invoke `prophet forecast evaluate` and parse its output."""
    prophet = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "prophet"
    cmd = [
        str(prophet), "forecast", "evaluate",
        "--submission", str(submission_path),
        "--actuals", str(actuals_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip(), "stdout": proc.stdout.strip()}
    out = proc.stdout
    # The evaluator prints a friendly summary. Try to scrape numbers.
    result = {"raw_stdout": out.strip()}
    for line in out.splitlines():
        if "brier" in line.lower():
            for tok in line.replace(":", " ").replace("=", " ").split():
                try:
                    result["brier_score"] = float(tok)
                    break
                except ValueError:
                    pass
        if "matched" in line.lower():
            for tok in line.split():
                if tok.isdigit():
                    result.setdefault("n_matched", int(tok))
                    break
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default="data/resolved.json")
    parser.add_argument("--actuals", default="data/actuals.json")
    parser.add_argument("--out-dir", default="data/predictions")
    parser.add_argument(
        "--variants",
        default="uniform_prior,single_llm",
        help="comma-separated variant names",
    )
    parser.add_argument(
        "--max-workers", type=int, default=5,
        help="concurrent API calls per variant (default 5)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    events_path = Path(args.events)
    actuals_path = Path(args.actuals)
    out_dir = Path(args.out_dir)

    events = json.loads(events_path.read_text())
    actuals = json.loads(actuals_path.read_text())
    log.info("loaded %d events, %d actuals", len(events), len(actuals))

    variant_names = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in variant_names if v not in VARIANTS]
    if unknown:
        log.error("unknown variants: %s (known: %s)", unknown, list(VARIANTS))
        return 2

    summary: list[dict] = []
    for name in variant_names:
        log.info("=== running variant %r ===", name)
        preds = run_variant(name, events, max_workers=args.max_workers)
        sub_path = out_dir / f"{name}.json"
        write_submission(preds, sub_path)
        log.info("wrote %d predictions to %s", len(preds), sub_path)
        result = evaluate(sub_path, actuals_path)
        log.info("evaluator result: %s", result)
        # Also compute Brier locally as a cross-check.
        local_brier = (
            sum((p["p_yes"] - actuals.get(p["market_ticker"], 0.5)) ** 2 for p in preds)
            / len(preds)
        )
        summary.append({
            "variant": name,
            "n_predictions": len(preds),
            "brier_local": round(local_brier, 6),
            "evaluator_result": result,
        })

    # Print the comparison table
    print("\n=== Variant comparison ===")
    print(f"{'variant':<20} {'n':>5} {'brier_local':>12}  {'evaluator':>30}")
    for row in summary:
        ev = row["evaluator_result"]
        ev_str = (
            f"brier={ev.get('brier_score', '?')}"
            if "brier_score" in ev
            else (ev.get("error", "?")[:30])
        )
        print(
            f"{row['variant']:<20} {row['n_predictions']:>5} "
            f"{row['brier_local']:>12.6f}  {ev_str:>30}",
        )

    summary_path = out_dir / "backtest_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    log.info("summary written to %s", summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
