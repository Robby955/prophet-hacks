#!/usr/bin/env python3
"""E3: retrieval count sweep.

Runs the production multi_outcome_retrieval variant 4 times with
different Brave Search result counts (3, 5, 8, 10) on the 26-event
resolved set. Measures single-binary + multi-class Brier per count.

Tests OpenForecaster's reported plateau-at-5 finding on our data.

Output: data/predictions/retrieval_sweep_{k}.json per count, plus
data/predictions/retrieval_sweep_summary.json with the comparison.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import forecast_track  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"
COUNTS = [3, 5, 8, 10]

log = logging.getLogger("retrieval_sweep")


def _monkey_patch_count(k: int):
    """Temporarily replace _brave_search to use the desired count."""
    original = forecast_track._brave_search
    def patched(query: str, count: int = 5) -> list[dict]:
        return original(query, count=k)
    forecast_track._brave_search = patched
    return original


def run_with_count(events: list[dict], k: int) -> list[dict]:
    original = _monkey_patch_count(k)
    preds: list[dict] = []
    try:
        log.info("running k=%d on %d events", k, len(events))

        def _one(ev: dict) -> dict:
            t = ev["market_ticker"]
            try:
                r = forecast_track.predict_multi_outcome_retrieval(ev)
            except Exception as ex:
                log.warning("[k=%d] %s raised: %s", k, t, ex)
                r = {"p_yes": 0.5, "rationale": f"error: {ex}",
                     "probabilities": [], "evidence_urls": []}
            return {
                "market_ticker": t,
                "p_yes": float(r["p_yes"]),
                "rationale": str(r.get("rationale", ""))[:300],
                "probabilities": r.get("probabilities") or [],
                "evidence_urls": (r.get("evidence_urls") or [])[:k + 2],
            }

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(_one, e) for e in events]
            done = 0
            for fut in as_completed(futures):
                preds.append(fut.result())
                done += 1
                if done % 5 == 0:
                    log.info("  [k=%d] %d/%d", k, done, len(events))
    finally:
        forecast_track._brave_search = original
    return preds


def score(preds: list[dict], actuals: dict, events_by_ticker: dict) -> dict:
    bins = []
    multis = []
    multi_only = []
    for r in preds:
        t = r["market_ticker"]
        a = actuals.get(t)
        if a is None:
            continue
        bins.append((float(r["p_yes"]) - float(a)) ** 2)
        ev = events_by_ticker.get(t, {})
        ro = ev.get("resolved_outcome", {})
        ro_val = ro.get("value", []) if isinstance(ro, dict) else []
        winner = ro_val[0] if ro_val else None
        outcomes = ev.get("outcomes", [])
        if winner and outcomes and r.get("probabilities"):
            pmap = {p["market"]: float(p["probability"]) for p in r["probabilities"]}
            mb = sum(
                (pmap.get(o, 1.0 / len(outcomes)) - (1.0 if o == winner else 0.0)) ** 2
                for o in outcomes
            )
            multis.append(mb)
            if len(outcomes) > 2:
                multi_only.append(mb)
    return {
        "binary_mean": round(sum(bins) / max(len(bins), 1), 5),
        "multi_mean": round(sum(multis) / max(len(multis), 1), 5),
        "multi_only_mean": round(sum(multi_only) / max(len(multi_only), 1), 5),
        "n_bin": len(bins),
        "n_multi": len(multis),
        "n_multi_only": len(multi_only),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    events = json.load(open(DATA / "resolved.json"))
    actuals = json.load(open(DATA / "actuals.json"))
    events_by_ticker = {e["market_ticker"]: e for e in events}

    results = []
    for k in COUNTS:
        t0 = time.monotonic()
        preds = run_with_count(events, k)
        elapsed = time.monotonic() - t0
        sc = score(preds, actuals, events_by_ticker)
        out = PRED / f"retrieval_sweep_k{k}.json"
        out.write_text(json.dumps({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "k": k,
            "predictions": preds,
        }, indent=2))
        log.info("k=%d done in %.1fs: binary=%.5f multi=%.5f", k, elapsed, sc["binary_mean"], sc["multi_mean"])
        results.append({"k": k, "elapsed_sec": round(elapsed, 1), **sc})

    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results": results,
    }
    (PRED / "retrieval_sweep_summary.json").write_text(json.dumps(summary, indent=2))
    print()
    print(f"{'k':>3} {'binary':>10} {'multi':>10} {'multi-only':>12} {'n_bin':>6} {'sec':>6}")
    for r in results:
        print(f"{r['k']:>3} {r['binary_mean']:>10.5f} {r['multi_mean']:>10.5f} "
              f"{r['multi_only_mean']:>12.5f} {r['n_bin']:>6} {r['elapsed_sec']:>6.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
