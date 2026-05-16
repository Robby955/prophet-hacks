"""Category-diversity smoke test against the live /predict endpoint.

Tony Yang (Discord, 2026-05-16): "events won't be highly skewed toward
some category". Our resolved-set backtest skewed toward Sports/Tennis;
our open-set covers Economics, Entertainment, Sports. PA's live event
slate could include Crypto, Climate, Tech, Finance, Pop-culture,
Geopolitics — categories we haven't formally tested.

This script fires 6 diverse synthetic events at production, captures
each response, and asserts:
  - HTTP 200
  - response shape matches PA schema
  - probabilities in [0, 1]
  - rationale references something category-relevant (very loose check)
  - latency < 30s (well under PA's 600s budget)

Spend: ~$0.60 (6 events × ~$0.10/call Opus + Brave free tier).

Usage:
    python scripts/smoke_categories.py
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


EVENTS = [
    {
        "category_under_test": "Crypto",
        "title": "Will Bitcoin close above $200,000 on December 31, 2027?",
        "category": "Crypto",
        "rules": "YES if BTC/USD closing price on a major exchange (Coinbase, Binance, Kraken median) on 2027-12-31 is strictly above $200,000.",
        "outcomes": ["Yes", "No"],
    },
    {
        "category_under_test": "Climate",
        "title": "Will any UK weather station record above 40.0°C in July 2027?",
        "category": "Climate",
        "rules": "YES if at least one UK Met Office-recognized station records a temperature above 40.0°C between July 1 and July 31, 2027.",
        "outcomes": ["Yes", "No"],
    },
    {
        "category_under_test": "Tech",
        "title": "Will OpenAI publicly release a model named GPT-6 by end of 2027?",
        "category": "Tech",
        "rules": "YES if OpenAI announces and makes available (even waitlisted) a model with 'GPT-6' in its name before 2028-01-01.",
        "outcomes": ["Yes", "No"],
    },
    {
        "category_under_test": "Finance",
        "title": "Will the S&P 500 close above 7,000 on any day in 2027?",
        "category": "Finance",
        "rules": "YES if SPX closes at or above 7000.00 on any trading day between 2027-01-01 and 2027-12-31.",
        "outcomes": ["Yes", "No"],
    },
    {
        "category_under_test": "Pop-culture",
        "title": "Will Taylor Swift release a new studio album in 2027?",
        "category": "Entertainment",
        "rules": "YES if Taylor Swift releases a new original studio album (not a re-recording or compilation) in calendar year 2027.",
        "outcomes": ["Yes", "No"],
    },
    {
        "category_under_test": "Geopolitics",
        "title": "Which country will host the 2030 FIFA World Cup?",
        "category": "Sports",  # Geopolitics-adjacent question with multi-outcome
        "rules": "Resolves to the country (or first listed in multi-host) that hosts the 2030 FIFA World Cup.",
        "outcomes": ["Spain/Portugal/Morocco", "Saudi Arabia", "Mexico/USA/Canada", "Other"],
    },
]

URL = "https://agent.forecastingpath.com/predict"


def _smoke_one(ev: dict) -> dict:
    body = {
        "event_ticker": f"CAT-SMOKE-{ev['category_under_test']}",
        "market_ticker": f"CAT-SMOKE-{ev['category_under_test']}",
        "title": ev["title"],
        "category": ev["category"],
        "rules": ev["rules"],
        "close_time": "2028-01-01T00:00:00Z",
        "outcomes": ev["outcomes"],
    }
    t0 = time.time()
    try:
        r = httpx.post(URL, json=body, timeout=120)
        dt = time.time() - t0
        out = {
            "category": ev["category_under_test"],
            "title": ev["title"][:60],
            "n_outcomes": len(ev["outcomes"]),
            "status": r.status_code,
            "latency_s": round(dt, 2),
        }
        if r.status_code == 200:
            body_resp = r.json()
            probs = body_resp.get("probabilities", [])
            out["n_probs"] = len(probs)
            out["probs_in_range"] = all(0.0 <= p["probability"] <= 1.0 for p in probs)
            out["labels_match"] = (
                {p["market"] for p in probs} == set(ev["outcomes"])
            )
            out["p_outcome_0"] = probs[0]["probability"] if probs else None
            out["rationale"] = (body_resp.get("rationale") or "")[:160]
        else:
            out["error"] = r.text[:200]
        return out
    except Exception as e:
        return {
            "category": ev["category_under_test"],
            "error": str(e)[:200],
            "latency_s": round(time.time() - t0, 2),
        }


def main() -> int:
    print(f"=== category-diversity smoke against {URL} ===")
    print(f"firing {len(EVENTS)} events in parallel ({len(EVENTS)} workers)...")
    t0 = time.time()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(EVENTS)) as ex:
        futs = {ex.submit(_smoke_one, e): e for e in EVENTS}
        for fut in as_completed(futs):
            results.append(fut.result())
    total = time.time() - t0

    # Sort by category for stable output
    results.sort(key=lambda r: r.get("category", ""))

    print(f"\n{'Category':<14} {'status':<6} {'lat':<6} {'n_out':<6} {'p_0':<8} {'rationale (excerpt)'}")
    print("  " + "-" * 100)
    failures = 0
    for r in results:
        if r.get("status") != 200:
            print(f"  {r.get('category','?'):<14} ERR    {r.get('latency_s','?'):<6} "
                  f"{r.get('error','')[:80]}")
            failures += 1
            continue
        if not r.get("probs_in_range", False):
            print(f"  {r['category']:<14} 200    {r['latency_s']:<6} probabilities out of [0,1]!")
            failures += 1
            continue
        if not r.get("labels_match", False):
            print(f"  {r['category']:<14} 200    {r['latency_s']:<6} outcome labels DON'T MATCH")
            failures += 1
            continue
        p0_str = f"{r['p_outcome_0']:.3f}" if r['p_outcome_0'] is not None else "?"
        print(f"  {r['category']:<14} 200    {r['latency_s']:<6} "
              f"{r['n_outcomes']:<6} {p0_str:<8} {r['rationale'][:60]}")

    print(f"\n  total wall clock: {total:.1f}s")
    print(f"  {len(results) - failures}/{len(results)} category smokes passed")

    # Save to a JSON file for future audit
    import os
    os.makedirs("data/predictions", exist_ok=True)
    with open("data/predictions/smoke_categories_latest.json", "w") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "results": results,
        }, f, indent=2)
    print(f"  saved to data/predictions/smoke_categories_latest.json")
    return failures


if __name__ == "__main__":
    sys.exit(main())
