"""Concurrent load test against the live /predict endpoint.

Verifies the production agent can keep up with Prophet Arena's tick
cadence. PA's contract: 10 minutes per event, one event per request.
PA might queue several events in parallel; we don't know the max
concurrency they'll send. This script fires N concurrent POST /predict
calls and reports per-call latency + total wall clock + success rate.

Usage:
    python scripts/stress_test.py
    python scripts/stress_test.py --url http://localhost:8000/predict --n 10
    python scripts/stress_test.py --workers 8

Cost: each call hits real Brave + Opus 4.7 (~$0.10/call). Default N=5
costs ~$0.50.

History:
    2026-05-16 — first run: 5 concurrent calls, 5/5 OK, 7.2s wall clock,
    max single-call latency 7.2s. Production well within PA's 10-min
    deadline.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


DEFAULT_EVENTS = [
    {
        "title": "Will the Fed cut rates at the December 2026 meeting?",
        "category": "Economics",
        "close_time": "2026-12-31T23:59:59Z",
        "outcomes": ["Yes", "No"],
    },
    {
        "title": "Will SpaceX reach Mars orbit by 2030?",
        "category": "Tech",
        "close_time": "2030-12-31T23:59:59Z",
        "outcomes": ["Yes", "No"],
    },
    {
        "title": "Will Apple ship a Vision Pro 2 in 2026?",
        "category": "Tech",
        "close_time": "2026-12-31T23:59:59Z",
        "outcomes": ["Yes", "No"],
    },
    {
        "title": "Which party wins the 2028 US Presidential Election?",
        "category": "Politics",
        "close_time": "2028-11-08T23:59:59Z",
        "outcomes": ["Democratic", "Republican", "Third party"],
    },
    {
        "title": "Will AGI be declared by a top lab before 2030?",
        "category": "Tech",
        "close_time": "2030-12-31T23:59:59Z",
        "outcomes": ["Yes", "No"],
    },
]


def _stress(url: str, n: int, workers: int) -> int:
    events = (DEFAULT_EVENTS * ((n + len(DEFAULT_EVENTS) - 1) // len(DEFAULT_EVENTS)))[:n]
    for i, e in enumerate(events):
        e["event_ticker"] = f"STRESS-{i}"
        e["market_ticker"] = f"STRESS-{i}"

    print(f"firing {len(events)} concurrent POST {url} (workers={workers})")
    t0 = time.time()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(httpx.post, url, json=e, timeout=300): e
            for e in events
        }
        for fut in as_completed(futs):
            e = futs[fut]
            t1 = time.time()
            try:
                r = fut.result()
                body = r.json() if r.status_code == 200 else {}
                probs = body.get("probabilities", [])
                results.append({
                    "ticker": e["market_ticker"],
                    "status": r.status_code,
                    "latency_s": round(t1 - t0, 2),
                    "n_outcomes": len(e["outcomes"]),
                    "n_probs_returned": len(probs),
                    "p_first_outcome": probs[0]["probability"] if probs else None,
                })
            except Exception as ex_:
                results.append({
                    "ticker": e["market_ticker"],
                    "error": str(ex_)[:160],
                    "latency_s": round(t1 - t0, 2),
                })

    total = time.time() - t0
    oks = [r for r in results if r.get("status") == 200]
    errs = [r for r in results if r.get("status") != 200]
    max_lat = max((r["latency_s"] for r in oks), default=0.0)

    print()
    print(f"=== Results ===")
    print(f"{'ticker':<12} {'status':<7} {'latency':<8} {'n_out':<6} {'p_first':<7}")
    for r in sorted(results, key=lambda x: x.get("latency_s", 999)):
        if r.get("status") == 200:
            p = r.get("p_first_outcome", "?")
            p_str = f"{p:.3f}" if isinstance(p, (int, float)) else str(p)
            print(f"  {r['ticker']:<12} {r['status']:<7} {r['latency_s']:<8} "
                  f"{r['n_outcomes']:<6} {p_str:<7}")
        else:
            print(f"  {r['ticker']:<12} ERR     {r['latency_s']:<8} "
                  f"{r.get('error', '?')[:60]}")
    print()
    print(f"  {len(oks)}/{len(results)} succeeded; "
          f"wall clock {total:.1f}s; max single-call latency {max_lat:.1f}s")
    print(f"  PA per-event budget: 600s. We're at {100*max_lat/600:.1f}% of budget.")
    return 0 if (errs == [] and max_lat < 60.0) else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="https://agent.forecastingpath.com/predict")
    p.add_argument("--n", type=int, default=5, help="number of concurrent calls")
    p.add_argument("--workers", type=int, default=5)
    args = p.parse_args()
    return _stress(args.url, args.n, args.workers)


if __name__ == "__main__":
    sys.exit(main())
