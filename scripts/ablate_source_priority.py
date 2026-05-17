#!/usr/bin/env python3
"""E4: source priority ablation.

Three variants on the same production pipeline:

  A. broad: no domain priority (rank Brave results in returned order)
  B. official: .gov / .edu / exchange-first (current production)
  C. exchanges_only: only kalshi.com / polymarket.com if any present,
     else fall through to broad

Same model + prompt + retrieval count (5). Only the dedupe/priority
order changes. Tests whether source ranking matters for Brier on the
26-event resolved set.
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

log = logging.getLogger("source_priority")
PRED = Path("data/predictions")


def patch_priority(mode: str):
    """Replace _domain_priority to implement the requested mode."""
    if mode == "broad":
        def prio(d: str) -> int:
            return 99
    elif mode == "official":
        # current production behavior; restore original
        return forecast_track._domain_priority
    elif mode == "exchanges_only":
        def prio(d: str) -> int:
            d = (d or "").lower()
            if "kalshi" in d or "polymarket" in d:
                return 0
            return 99
    else:
        raise ValueError(f"unknown mode {mode}")
    return prio


def run_variant(events: list[dict], mode: str) -> list[dict]:
    original = forecast_track._domain_priority
    forecast_track._domain_priority = patch_priority(mode) if mode != "official" else original

    preds: list[dict] = []
    try:
        log.info("running mode=%s on %d events", mode, len(events))

        def _one(ev: dict) -> dict:
            t = ev["market_ticker"]
            try:
                r = forecast_track.predict_multi_outcome_retrieval(ev)
            except Exception as ex:
                log.warning("[%s] %s raised: %s", mode, t, ex)
                r = {"p_yes": 0.5, "rationale": f"error: {ex}",
                     "probabilities": [], "evidence_urls": []}
            return {
                "market_ticker": t,
                "p_yes": float(r["p_yes"]),
                "rationale": str(r.get("rationale", ""))[:300],
                "probabilities": r.get("probabilities") or [],
                "evidence_urls": (r.get("evidence_urls") or [])[:6],
            }

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(_one, e) for e in events]
            done = 0
            for fut in as_completed(futures):
                preds.append(fut.result())
                done += 1
                if done % 5 == 0:
                    log.info("  [%s] %d/%d", mode, done, len(events))
    finally:
        forecast_track._domain_priority = original
    return preds


def score(preds, actuals, events_by_ticker):
    bins, multis = [], []
    for r in preds:
        t = r["market_ticker"]
        a = actuals.get(t)
        if a is None: continue
        bins.append((float(r["p_yes"]) - float(a)) ** 2)
        ev = events_by_ticker.get(t, {})
        ro = ev.get("resolved_outcome", {})
        ro_val = ro.get("value", []) if isinstance(ro, dict) else []
        winner = ro_val[0] if ro_val else None
        outcomes = ev.get("outcomes", [])
        if winner and outcomes and r.get("probabilities"):
            pmap = {p["market"]: float(p["probability"]) for p in r["probabilities"]}
            mb = sum(
                (pmap.get(o, 1.0/len(outcomes)) - (1.0 if o == winner else 0.0)) ** 2
                for o in outcomes
            )
            multis.append(mb)
    return {
        "binary": round(sum(bins)/max(len(bins),1), 5),
        "multi": round(sum(multis)/max(len(multis),1), 5),
        "n_bin": len(bins),
        "n_multi": len(multis),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    events = json.load(open("data/resolved.json"))
    actuals = json.load(open("data/actuals.json"))
    events_by_ticker = {e["market_ticker"]: e for e in events}

    results = []
    for mode in ["broad", "official", "exchanges_only"]:
        t0 = time.monotonic()
        preds = run_variant(events, mode)
        elapsed = time.monotonic() - t0
        sc = score(preds, actuals, events_by_ticker)
        out = PRED / f"source_priority_{mode}.json"
        out.write_text(json.dumps({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": mode,
            "predictions": preds,
        }, indent=2))
        log.info("%s done in %.1fs: binary=%.5f multi=%.5f", mode, elapsed, sc["binary"], sc["multi"])
        results.append({"mode": mode, "elapsed_sec": round(elapsed, 1), **sc})

    print()
    print(f"{'mode':<20} {'binary':>10} {'multi':>10} {'sec':>6}")
    for r in results:
        print(f"{r['mode']:<20} {r['binary']:>10.5f} {r['multi']:>10.5f} {r['elapsed_sec']:>6.1f}")

    (PRED / "source_priority_summary.json").write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
