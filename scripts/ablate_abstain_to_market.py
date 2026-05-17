#!/usr/bin/env python3
"""E1: abstain-to-market variant.

Strategy per PA organizer hint: "only make a prediction when you are
confident enough, otherwise just use the market probability."

Two components:

1. Extract any cited market-implied probabilities from the evidence text
   via a lightweight Haiku call. (Brave returns snippets that often quote
   "Polymarket has X at 65%", "implied 0.47", "betting odds 2/1", etc.)
2. Apply abstain policy: if a market price is cited AND |p_model -
   p_market| < threshold, return p_market instead of p_model.

Output saved to data/predictions/abstain_to_market.json with both the
production prediction and the policy decision per event, scored at
multiple thresholds (0.05, 0.10, 0.15).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic  # noqa: E402
import forecast_track  # noqa: E402

log = logging.getLogger("abstain")

_HAIKU = "claude-haiku-4-5-20251001"

_EXTRACT_SYSTEM = """\
You extract market-implied probabilities from evidence text. Given an event,
its outcomes, and 1-5 evidence snippets, find any explicit probability or
price quotes for the FIRST outcome only.

Patterns to match:
- "Polymarket has X at 65%" → 0.65
- "implied probability 47%" → 0.47
- "trading at $0.32" (Kalshi-style price) → 0.32
- "betting odds 2/1" → 0.33 (1/(1+2))
- "9-point favorite" → keep at 0.5 unless explicit prob given

Output ONLY JSON:
  {"p_market_outcome0": <float between 0 and 1, or null if no cite found>,
   "source": "<brief quote or null>"}
If no market-implied probability is cited for outcomes[0], return null.
Do not infer; only extract explicitly cited numbers.
"""


def extract_market_for_event(event: dict, prod_row: dict) -> dict:
    client = Anthropic()
    outcomes = event.get("outcomes") or []
    outcome0 = outcomes[0] if outcomes else "?"
    evidence_urls = prod_row.get("evidence_urls") or []
    rationale = prod_row.get("rationale", "")[:600]

    user_text = (
        f"EVENT: {event.get('title','')}\n\n"
        f"OUTCOME OF INTEREST (outcomes[0]): {outcome0}\n\n"
        f"EVIDENCE SOURCES the forecaster cited:\n"
        + "\n".join(f"  - {u}" for u in evidence_urls[:5])
        + f"\n\nFORECASTER RATIONALE (may contain cited prices):\n{rationale}\n\n"
        "Extract any explicitly cited market price/odds/probability for outcome0."
    )

    try:
        resp = client.messages.create(
            model=_HAIKU,
            max_tokens=300,
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": user_text}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as ex:
        return {"p_market_outcome0": None, "source": None, "error": str(ex)}

    # Best-effort JSON parse
    m = re.search(r"\{[^}]+\}", text, re.DOTALL)
    if not m:
        return {"p_market_outcome0": None, "source": None}
    try:
        d = json.loads(m.group(0))
        p = d.get("p_market_outcome0")
        if p is not None:
            try:
                p = float(p)
                if not (0.0 <= p <= 1.0):
                    p = None
            except (TypeError, ValueError):
                p = None
        return {"p_market_outcome0": p, "source": d.get("source")}
    except json.JSONDecodeError:
        return {"p_market_outcome0": None, "source": None}


def policy_brier(prod_rows: list[dict], market_prices: dict[str, float],
                 actuals: dict, threshold: float) -> dict:
    """For each event, decide whether to use p_model or p_market.

    Returns: {mean_brier, n_traded, n_abstained, n_market_known}.
    """
    losses = []
    n_traded = n_abstained = n_market_known = 0
    for r in prod_rows:
        t = r["market_ticker"]
        a = actuals.get(t)
        if a is None:
            continue
        p_model = float(r["p_yes"])
        p_market = market_prices.get(t)
        if p_market is not None:
            n_market_known += 1
            if abs(p_model - p_market) < threshold:
                used = p_market
                n_abstained += 1
            else:
                used = p_model
                n_traded += 1
        else:
            used = p_model  # no abstain option available
            n_traded += 1
        losses.append((used - float(a)) ** 2)
    return {
        "mean_brier": sum(losses) / max(len(losses), 1),
        "n_total": len(losses),
        "n_traded": n_traded,
        "n_abstained": n_abstained,
        "n_market_known": n_market_known,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing", file=sys.stderr); return 2

    events = json.load(open("data/resolved.json"))
    actuals = json.load(open("data/actuals.json"))
    prod = json.load(open("data/predictions/multi_outcome_retrieval.json"))
    prod_rows = prod.get("predictions", prod)
    prod_by_ticker = {r["market_ticker"]: r for r in prod_rows}

    log.info("extracting cited market prices from evidence on %d events", len(events))
    market_prices: dict[str, float] = {}
    sources: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(extract_market_for_event, e, prod_by_ticker[e["market_ticker"]]): e
                   for e in events if e["market_ticker"] in prod_by_ticker}
        for fut in as_completed(futures):
            ev = futures[fut]
            r = fut.result()
            t = ev["market_ticker"]
            if r.get("p_market_outcome0") is not None:
                market_prices[t] = r["p_market_outcome0"]
                sources[t] = r.get("source") or ""
            if (len(market_prices) + 1) % 5 == 0 or len(market_prices) == 0:
                log.info("  %d/%d so far, %d with market price", len(futures), len(events), len(market_prices))

    log.info("market price extracted for %d/%d events", len(market_prices), len(events))

    # Score at multiple thresholds
    thresholds = [0.05, 0.10, 0.15, 0.20]
    rows = []
    for thr in thresholds:
        r = policy_brier(prod_rows, market_prices, actuals, thr)
        rows.append({"threshold": thr, **r})
    # Baseline: always trade
    baseline = policy_brier(prod_rows, market_prices, actuals, threshold=0.0)
    rows.insert(0, {"threshold": 0.0, **baseline})

    print()
    print(f"market prices extracted: {len(market_prices)}/{len(events)} events")
    print()
    print(f"{'threshold':>10} {'mean_brier':>10} {'n_traded':>9} {'n_abstain':>10} {'delta':>10}")
    for r in rows:
        delta = r["mean_brier"] - baseline["mean_brier"]
        print(f"{r['threshold']:>10.2f} {r['mean_brier']:>10.5f} {r['n_traded']:>9} {r['n_abstained']:>10} {delta:>+10.5f}")

    Path("data/predictions/abstain_to_market.json").write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_events": len(events),
        "n_market_known": len(market_prices),
        "market_prices": market_prices,
        "sources": sources,
        "results": rows,
    }, indent=2))
    print("wrote data/predictions/abstain_to_market.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
