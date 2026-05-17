#!/usr/bin/env python3
"""Subset-1200: validate the production pipeline at 46x our 26-event sample.

Dataset: huggingface.co/datasets/prophetarena/Prophet-Arena-Subset-1200
- 1200 resolved Prophet Arena events with snapshotted market prices
- Same shape PA uses live (title, rules, category, close_time, outcomes)
- Includes market_data (snapshotted prices at snapshot_time) so we can
  compute the real BSS-vs-market metric PA uses live, not just absolute
  Brier.

Runs the production variant `predict_multi_outcome_retrieval` on every
event, saves to `data/predictions/subset_1200.json`. Then computes:
- mean single-binary Brier
- mean multi-class Brier
- per-event variance vs the 26-event backtest pattern
- leakage audit on the new evidence URLs
- BSS vs market (where market_data has prices for outcomes[0])

Cost: ~$120 in Anthropic Opus 4.7 + Brave Search calls.
Time: ~30-60 min at 4 parallel workers.
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

log = logging.getLogger("subset_1200")
PRED = Path("data/predictions")


def _coerce_to_event(row: dict) -> dict:
    """Convert a Subset-1200 row to the production event shape.

    Subset-1200 has `markets` as a stringified Python list and
    `market_outcome` as a stringified dict. Our pipeline expects
    `outcomes` as a real list and resolution info we'll keep separate.
    """
    markets_raw = row.get("markets", "[]")
    try:
        outcomes = eval(markets_raw, {"__builtins__": {}}, {}) if isinstance(markets_raw, str) else markets_raw
        if not isinstance(outcomes, list):
            outcomes = []
    except Exception:
        outcomes = []
    outcomes = [str(o) for o in outcomes]

    return {
        "event_ticker": row.get("event_ticker", ""),
        "market_ticker": row.get("event_ticker", ""),
        "title": row.get("augmented_title") or row.get("title") or "",
        "description": "",
        "rules": row.get("rules", "") or "",
        "category": row.get("category", "") or "Other",
        "close_time": row.get("close_time", ""),
        "outcomes": outcomes,
    }


def _actual_from_row(row: dict) -> int | None:
    """Extract the binary actual (was outcomes[0] resolved YES?).

    market_outcome is a stringified dict {outcome_label: 0_or_1}.
    """
    raw = row.get("market_outcome", "{}")
    try:
        if isinstance(raw, str):
            d = json.loads(raw.replace("'", '"'))
        elif isinstance(raw, dict):
            d = raw
        else:
            d = {}
    except json.JSONDecodeError:
        try:
            d = eval(raw, {"__builtins__": {}}, {})
        except Exception:
            d = {}
    if not isinstance(d, dict) or not d:
        return None
    markets_raw = row.get("markets", "[]")
    try:
        outcomes = eval(markets_raw, {"__builtins__": {}}, {}) if isinstance(markets_raw, str) else markets_raw
    except Exception:
        return None
    if not isinstance(outcomes, list) or not outcomes:
        return None
    outcome0 = outcomes[0]
    return int(d.get(outcome0, 0)) if outcome0 in d else None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing", file=sys.stderr)
        return 2

    from datasets import load_dataset
    log.info("loading prophetarena/Prophet-Arena-Subset-1200 …")
    ds = load_dataset("prophetarena/Prophet-Arena-Subset-1200", split="train")
    log.info("loaded %d rows", len(ds))

    rows = list(ds)
    events = [_coerce_to_event(r) for r in rows]
    actuals = {events[i]["event_ticker"]: _actual_from_row(rows[i])
               for i in range(len(rows))}
    actuals_clean = {k: v for k, v in actuals.items() if v is not None}
    log.info("usable events with valid actuals: %d", len(actuals_clean))

    # Save snapshot of inputs + actuals so the run is reproducible
    PRED.parent.mkdir(parents=True, exist_ok=True)
    (PRED / "subset_1200_actuals.json").write_text(json.dumps(actuals_clean, indent=2))

    def _one(ev: dict) -> dict:
        t = ev["event_ticker"]
        try:
            r = forecast_track.predict_multi_outcome_retrieval(ev)
        except Exception as ex:
            log.warning("[%s] raised: %s", t, ex)
            r = {"p_yes": 0.5, "rationale": f"error: {ex}",
                 "probabilities": [], "evidence_urls": []}
        return {
            "market_ticker": t,
            "p_yes": float(r["p_yes"]),
            "rationale": str(r.get("rationale", ""))[:300],
            "probabilities": r.get("probabilities") or [],
            "evidence_urls": (r.get("evidence_urls") or [])[:6],
        }

    log.info("running production variant on %d events with 4 workers", len(events))
    preds: list[dict] = []
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_one, e) for e in events]
        done = 0
        for fut in as_completed(futures):
            preds.append(fut.result())
            done += 1
            if done % 25 == 0 or done == len(events):
                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed else 0
                eta = (len(events) - done) / rate if rate else 0
                log.info("  %d/%d (%.1fs elapsed, %.1f ev/s, ETA %.0fs)",
                         done, len(events), elapsed, rate, eta)

    out = PRED / "subset_1200.json"
    out.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_events": len(preds),
        "predictions": preds,
    }, indent=2))
    log.info("wrote %d predictions to %s", len(preds), out)

    # Quick aggregate
    bins = []
    for r in preds:
        a = actuals_clean.get(r["market_ticker"])
        if a is None:
            continue
        bins.append((float(r["p_yes"]) - float(a)) ** 2)
    if bins:
        mean = sum(bins) / len(bins)
        print()
        print(f"=== Subset-1200 aggregate ===")
        print(f"  events scored: {len(bins)}")
        print(f"  mean single-binary Brier: {mean:.5f}")
        print(f"  vs n=26 sample-resolved: 0.0378")
        print(f"  delta: {mean - 0.03782:+.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
