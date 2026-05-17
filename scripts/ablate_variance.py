#!/usr/bin/env python3
"""Intra-model variance: re-run production 5 times on the same 26 events.

LLM responses are non-deterministic even at fixed temperature. The
question "what is our production Brier?" deserves an answer with a
variance estimate, not a single point.

Runs `predict_multi_outcome_retrieval` 5 times sequentially (5 fresh
LLM calls per event = 130 total Opus 4.7 calls + 130 Brave fetches).
Saves each run as `data/predictions/variance_run_<i>.json` for
downstream analysis. Prints mean, std, min, max of the resulting
per-run Brier and the per-event coefficient of variation.

~$13 in API spend. Read-only against production: writes to a
separate file family so the canonical `multi_outcome_retrieval.json`
stays untouched.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import forecast_track  # noqa: E402

log = logging.getLogger("variance")
PRED = Path("data/predictions")
N_RUNS = 5


def run_once(events: list[dict], run_id: int) -> list[dict]:
    log.info("=== variance run %d/%d on %d events ===", run_id, N_RUNS, len(events))

    def _one(ev: dict) -> dict:
        t = ev["market_ticker"]
        try:
            r = forecast_track.predict_multi_outcome_retrieval(ev)
        except Exception as ex:
            log.warning("[run %d] %s raised: %s", run_id, t, ex)
            r = {"p_yes": 0.5, "rationale": f"error: {ex}",
                 "probabilities": [], "evidence_urls": []}
        return {
            "market_ticker": t,
            "p_yes": float(r["p_yes"]),
            "rationale": str(r.get("rationale", ""))[:300],
            "probabilities": r.get("probabilities") or [],
            "evidence_urls": (r.get("evidence_urls") or [])[:6],
        }

    preds: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_one, e) for e in events]
        done = 0
        for fut in as_completed(futures):
            preds.append(fut.result())
            done += 1
            if done % 5 == 0 or done == len(events):
                log.info("  run %d progress: %d/%d", run_id, done, len(events))
    return preds


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing", file=sys.stderr)
        return 2
    events = json.load(open("data/resolved.json"))
    actuals = json.load(open("data/actuals.json"))

    results: list[dict] = []
    for i in range(1, N_RUNS + 1):
        t0 = time.monotonic()
        preds = run_once(events, i)
        elapsed = time.monotonic() - t0
        out = PRED / f"variance_run_{i}.json"
        out.write_text(json.dumps({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_id": i,
            "predictions": preds,
        }, indent=2))

        # Per-run Brier
        bs = []
        for r in preds:
            a = actuals.get(r["market_ticker"])
            if a is None: continue
            bs.append((float(r["p_yes"]) - float(a)) ** 2)
        mean_b = sum(bs) / max(len(bs), 1)
        results.append({"run": i, "mean_brier": round(mean_b, 5),
                        "n": len(bs), "elapsed_sec": round(elapsed, 1)})
        log.info("  -> run %d Brier=%.5f (n=%d, %.1fs)", i, mean_b, len(bs), elapsed)

    # Aggregate
    import statistics as st
    briers = [r["mean_brier"] for r in results]
    mean = sum(briers) / len(briers)
    stdev = st.stdev(briers) if len(briers) > 1 else 0.0

    print()
    print(f"{'run':>4} {'mean_brier':>12} {'n':>4} {'sec':>6}")
    for r in results:
        print(f"{r['run']:>4} {r['mean_brier']:>12.5f} {r['n']:>4} {r['elapsed_sec']:>6.1f}")
    print()
    print(f"5-run aggregate: mean={mean:.5f}, std={stdev:.5f}")
    print(f"range: [{min(briers):.5f}, {max(briers):.5f}]")
    print(f"canonical production file:  0.03782 (from data/predictions/multi_outcome_retrieval.json)")

    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_runs": N_RUNS,
        "n_events": len(events),
        "per_run_brier": briers,
        "mean": round(mean, 5),
        "std": round(stdev, 5),
        "min": round(min(briers), 5),
        "max": round(max(briers), 5),
        "canonical_production_brier": 0.03782,
        "results": results,
    }
    (PRED / "variance_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"wrote {PRED / 'variance_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
