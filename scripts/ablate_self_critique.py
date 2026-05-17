#!/usr/bin/env python3
"""Self-critique ablation: Opus 4.7 reviews its own first-pass forecast.

Two LLM passes per event:

1. Production prediction (Opus 4.7 + retrieval + market-anchor prompt).
2. Self-critique: present the first prediction + evidence back to Opus 4.7
   under a critique-oriented system prompt; emit revised probabilities.

Compares mean Brier (single-binary + multi-class) pre vs post critique on
the 26-event resolved set. Roughly $5 spend (26 events x 2 Opus calls).

Result saved to data/predictions/self_critique.json with both pre and post
distributions per event for later analysis.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic  # noqa: E402

import forecast_track  # noqa: E402

log = logging.getLogger("self_critique")

_CRITIQUE_MODEL = "claude-opus-4-7"

_CRITIQUE_SYSTEM = """\
You are an adversarial reviewer of probabilistic forecasts. You will be
shown an event, supporting evidence snippets, and another forecaster's
per-outcome probabilities. Your job is to find calibration problems and
revise.

Specifically check:
1. Is any probability too confident (>0.85) without specific corroborating
   evidence in the snippets? Pull it back toward 0.75.
2. Is the favorite under-confident (<0.55) when evidence cites market odds
   or a single dominant signal? Push it up.
3. Are longshots assigned <0.05 when the resolution rule allows surprise
   outcomes (extreme draws, late events, contested terms)? Floor at 0.05.
4. Do per-outcome probabilities together reflect the event's actual
   uncertainty, or do they look like a "default" template (e.g. one big
   number and a bunch of identical small ones)?
5. Resist vivid-narrative bias: low-probability scenarios that read well
   should not be moved up without evidence.

Output ONLY JSON of the shape:
  {"probabilities": {"<outcome label>": <float>, ...}, "rationale": "<one sentence on what you changed and why>"}
Probabilities do NOT need to sum to 1. Use all the outcome labels from
the input. If you decide the original forecast is well-calibrated and
needs no change, return it verbatim and say so in the rationale.
"""


def _critique_one(event: dict[str, Any], first_pass: dict[str, Any]) -> dict[str, Any]:
    """Run the second-pass critique using the same retrieved evidence."""
    client = Anthropic()
    outcomes = event.get("outcomes") or []

    # Reuse the same evidence the first-pass already had (cached in result).
    evidence_urls = first_pass.get("evidence_urls") or []
    rationale_v1 = first_pass.get("rationale", "")
    probs_v1 = first_pass.get("probabilities") or []

    user_msg = (
        f"EVENT TITLE: {event.get('title','')}\n\n"
        f"DESCRIPTION: {event.get('description','')}\n\n"
        f"RESOLUTION RULE: {event.get('rules','')}\n\n"
        f"OUTCOMES (use these labels exactly): {json.dumps(outcomes)}\n\n"
        f"FIRST-PASS FORECAST:\n"
        f"  rationale: {rationale_v1}\n"
        f"  probabilities: {json.dumps(probs_v1)}\n\n"
        f"EVIDENCE URLS available to the first-pass:\n"
        + "\n".join(f"  - {u}" for u in evidence_urls[:5])
        + "\n\nReview and revise. Output JSON only."
    )

    try:
        resp = client.messages.create(
            model=_CRITIQUE_MODEL,
            max_tokens=1500,
            system=_CRITIQUE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as ex:
        log.warning("[critique] %s: api error: %s", event.get("market_ticker"), ex)
        return {**first_pass, "critique_status": "api_error", "critique_error": str(ex)}

    parsed = forecast_track._parse_multi_outcome_json(text)
    if not parsed or not isinstance(parsed, dict):
        log.warning("[critique] %s: unparseable; keeping v1", event.get("market_ticker"))
        return {**first_pass, "critique_status": "unparseable", "critique_raw": text[:300]}

    new_probs_raw = parsed.get("probabilities") or {}
    new_rationale = parsed.get("rationale", "")[:300] if parsed.get("rationale") else ""

    # Match each LLM-emitted key back to a canonical outcome label.
    matched_probs: list[dict[str, float]] = []
    for raw_key, raw_val in (new_probs_raw.items() if isinstance(new_probs_raw, dict) else []):
        canon = forecast_track._match_outcome_label(str(raw_key), outcomes)
        if canon is not None:
            try:
                matched_probs.append({"market": canon, "probability": float(raw_val)})
            except (TypeError, ValueError):
                continue

    if not matched_probs:
        log.warning("[critique] %s: zero label matches; keeping v1", event.get("market_ticker"))
        return {**first_pass, "critique_status": "no_label_match"}

    # Apply same longshot floor + renorm as production.
    final_probs = forecast_track.apply_longshot_guard(matched_probs, len(outcomes))

    # outcome[0] p_yes for binary scoring compatibility.
    raw_p = float(final_probs[0]["probability"]) if final_probs else 0.5
    new_p_yes = max(0.01, min(0.99, raw_p))

    return {
        "market_ticker": event.get("market_ticker"),
        "p_yes_v1": first_pass["p_yes"],
        "p_yes": new_p_yes,
        "probabilities_v1": probs_v1,
        "probabilities": final_probs,
        "rationale_v1": rationale_v1,
        "rationale": new_rationale,
        "evidence_urls": evidence_urls,
        "critique_status": "revised",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Opus self-critique ablation on resolved events.",
    )
    parser.add_argument("--events", default="data/resolved.json")
    parser.add_argument("--actuals", default="data/actuals.json")
    parser.add_argument(
        "--output",
        default="data/predictions/self_critique.json",
        help="Output JSON path. Use a new name for replication runs.",
    )
    parser.add_argument("--max-workers", type=int, default=4)
    return parser.parse_args()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            text=True,
            timeout=2,
        ).strip()
    except Exception:
        return "unknown"


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing", file=sys.stderr)
        return 2

    events = json.load(open(args.events))
    actuals = json.load(open(args.actuals))

    # Run production first-pass on all events (parallel, 4 workers).
    log.info("running first-pass forecasts on %d events", len(events))
    first_passes: dict[str, dict[str, Any]] = {}

    def _first(ev: dict) -> tuple[str, dict]:
        t = ev["market_ticker"]
        try:
            r = forecast_track.predict_multi_outcome_retrieval(ev)
        except Exception as ex:
            log.warning("[first] %s raised: %s", t, ex)
            r = {"p_yes": 0.5, "rationale": f"error: {ex}",
                 "probabilities": [], "evidence_urls": []}
        return t, {"market_ticker": t, **r}

    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = [ex.submit(_first, e) for e in events]
        for fut in as_completed(futures):
            t, r = fut.result()
            first_passes[t] = r
            if len(first_passes) % 5 == 0:
                log.info("  first-pass progress: %d/%d", len(first_passes), len(events))

    # Run critique pass.
    log.info("running self-critique on %d events", len(events))
    critiques: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = {ex.submit(_critique_one, e, first_passes[e["market_ticker"]]): e
                   for e in events}
        for fut in as_completed(futures):
            ev = futures[fut]
            r = fut.result()
            critiques[ev["market_ticker"]] = r
            if len(critiques) % 5 == 0:
                log.info("  critique progress: %d/%d", len(critiques), len(events))

    # Score both.
    def brier_b(p, a): return (float(p) - float(a)) ** 2

    rows_pre, rows_post = [], []
    changed = 0
    for ev in events:
        t = ev["market_ticker"]
        actual = actuals.get(t)
        if actual is None:
            continue
        v1 = first_passes[t]
        v2 = critiques[t]
        rows_pre.append(brier_b(v1["p_yes"], actual))
        rows_post.append(brier_b(v2["p_yes"], actual))
        if abs(float(v1["p_yes"]) - float(v2["p_yes"])) > 0.001:
            changed += 1

    pre_mean = sum(rows_pre) / max(len(rows_pre), 1)
    post_mean = sum(rows_post) / max(len(rows_post), 1)

    print()
    print(f"first-pass mean Brier:  {pre_mean:.5f}  (n={len(rows_pre)})")
    print(f"after-critique mean:    {post_mean:.5f}")
    print(f"Δ (negative = critique improved): {post_mean - pre_mean:+.5f}")
    print(f"events where critique changed p_yes by >0.001: {changed}/{len(events)}")

    out = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": _git_sha(),
        "events_file": args.events,
        "actuals_file": args.actuals,
        "critique_model": _CRITIQUE_MODEL,
        "max_workers": args.max_workers,
        "first_pass_mean_brier_binary": round(pre_mean, 5),
        "critique_mean_brier_binary": round(post_mean, 5),
        "delta": round(post_mean - pre_mean, 5),
        "n_events_changed": changed,
        "predictions": list(critiques.values()),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
