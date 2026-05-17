#!/usr/bin/env python3
"""E2: one-call verification prompt variant.

Codex's suggestion: replace the failed two-call self-critique with a
single Opus 4.7 call that emits p_initial → verification field →
p_final in one structured response. Cheaper, same cognitive shape.

System prompt asks the model to (1) state an initial probability per
outcome, (2) write a verification step listing specific risks to that
initial reading, (3) emit a final probability that may differ.

Output saved to data/predictions/verification_prompt.json with
{p_yes_v1, p_yes_final, verification_text, ...} per event. Compares
mean Brier pre vs post verification.
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

from anthropic import Anthropic  # noqa: E402
import forecast_track  # noqa: E402

log = logging.getLogger("verif")

_SYSTEM = """\
You are a calibrated probabilistic forecaster with a built-in verification step.

For each event:

1. State an INITIAL per-outcome probability distribution (your first read).
2. Write a SHORT VERIFICATION step (2-4 sentences) that explicitly checks:
   - Is any probability >0.85 without specific evidence backing it?
   - Is the favorite under-confident given the evidence?
   - Are longshots floored at 0.05?
   - Do the numbers look like a "default template"?
3. State a FINAL per-outcome probability distribution. It MAY equal the
   initial if verification finds nothing to fix.

Output ONLY valid JSON:
{
  "probabilities_initial": {"<outcome>": float, ...},
  "verification": "<2-4 sentence self-check>",
  "probabilities": {"<outcome>": float, ...},
  "rationale": "<one-sentence summary>"
}

Probabilities do NOT need to sum to 1. Use ALL the outcome labels from the
input. Calibration scale: 0.50 (no view) - 0.60 (slight lean) - 0.70 (real
view) - 0.80 (strong evidence) - 0.90 (near-certain).
"""


def run_one(event: dict) -> dict:
    client = Anthropic()
    outcomes = event.get("outcomes") or []

    # Reuse production retrieval to keep the comparison apples-to-apples.
    try:
        query = forecast_track._build_query(event)
        results = forecast_track._brave_search(query, count=5)
        evidence_urls = [r.get("url") for r in results if r.get("url")][:5]
        user_text = forecast_track._build_retrieval_user_prompt(event, results)
    except Exception as ex:
        log.warning("[verif] %s retrieval failed: %s", event.get("market_ticker"), ex)
        evidence_urls = []
        user_text = (
            f"EVENT: {event.get('title','')}\n\n"
            f"OUTCOMES: {json.dumps(outcomes)}\n\n"
            f"DESCRIPTION: {event.get('description','')}\n\n"
            f"RULES: {event.get('rules','')}"
        )

    try:
        resp = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_text}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as ex:
        log.warning("[verif] %s api err: %s", event.get("market_ticker"), ex)
        return {
            "market_ticker": event.get("market_ticker"),
            "p_yes": 0.5, "p_yes_initial": 0.5,
            "rationale": f"error: {ex}",
            "verification": "", "probabilities": [],
            "evidence_urls": evidence_urls,
        }

    parsed = forecast_track._parse_multi_outcome_json(text)
    if not parsed or not isinstance(parsed, dict):
        log.warning("[verif] %s unparseable", event.get("market_ticker"))
        return {
            "market_ticker": event.get("market_ticker"),
            "p_yes": 0.5, "p_yes_initial": 0.5,
            "rationale": "llm error: unparseable", "verification": "",
            "probabilities": [], "evidence_urls": evidence_urls,
        }

    def to_list(probs_dict, outcomes):
        out = []
        for raw_key, raw_val in (probs_dict.items() if isinstance(probs_dict, dict) else []):
            canon = forecast_track._match_outcome_label(str(raw_key), outcomes)
            if canon is not None:
                try:
                    out.append({"market": canon, "probability": float(raw_val)})
                except (TypeError, ValueError):
                    continue
        return out

    initial_probs = to_list(parsed.get("probabilities_initial") or {}, outcomes)
    final_probs   = to_list(parsed.get("probabilities") or {}, outcomes)
    if not final_probs:
        final_probs = initial_probs

    # Apply production longshot floor + renorm to the FINAL set only.
    final_probs = forecast_track.apply_longshot_guard(final_probs, len(outcomes))
    p_initial = float(initial_probs[0]["probability"]) if initial_probs else 0.5
    p_final = float(final_probs[0]["probability"]) if final_probs else 0.5

    return {
        "market_ticker": event.get("market_ticker"),
        "p_yes": max(0.01, min(0.99, p_final)),
        "p_yes_initial": max(0.01, min(0.99, p_initial)),
        "probabilities_initial": initial_probs,
        "probabilities": final_probs,
        "verification": (parsed.get("verification", "") or "")[:300],
        "rationale": (parsed.get("rationale", "") or "")[:300],
        "evidence_urls": evidence_urls,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing", file=sys.stderr); return 2
    events = json.load(open("data/resolved.json"))
    actuals = json.load(open("data/actuals.json"))

    log.info("running verification-prompt variant on %d events", len(events))
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(run_one, e) for e in events]
        for fut in as_completed(futures):
            results.append(fut.result())
            if len(results) % 5 == 0:
                log.info("  %d/%d", len(results), len(events))

    # Score
    initial_bs, final_bs = [], []
    changed = 0
    for r in results:
        a = actuals.get(r["market_ticker"])
        if a is None: continue
        initial_bs.append((float(r["p_yes_initial"]) - float(a)) ** 2)
        final_bs.append((float(r["p_yes"]) - float(a)) ** 2)
        if abs(float(r["p_yes"]) - float(r["p_yes_initial"])) > 0.001:
            changed += 1

    pre = sum(initial_bs)/len(initial_bs)
    post = sum(final_bs)/len(final_bs)
    print()
    print(f"initial-pass mean Brier:        {pre:.5f}")
    print(f"after-verification mean Brier:  {post:.5f}")
    print(f"delta (negative = improvement): {post - pre:+.5f}")
    print(f"events where verification changed p_yes by >0.001: {changed}/{len(events)}")

    out = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "initial_mean_brier": round(pre, 5),
        "verification_mean_brier": round(post, 5),
        "delta": round(post - pre, 5),
        "n_changed": changed,
        "predictions": results,
    }
    Path("data/predictions/verification_prompt.json").write_text(json.dumps(out, indent=2))
    print("wrote data/predictions/verification_prompt.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
