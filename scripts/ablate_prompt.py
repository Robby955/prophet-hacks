"""Ablation harness for system-prompt variants.

The 2026-05-17 question: how much of the production Brier is the
market-odds-anchoring instruction, the calibration scale, or the
"don't invent details" rule? Same model (Opus 4.7), same retrieval
(Brave 5 chunks), same longshot floor — only the system prompt varies.

Variants tested (define in PROMPT_VARIANTS below; each must produce
the same JSON shape as the production prompt):

  v0_production           — the full current production prompt
  v1_no_anchor            — remove the market-odds anchoring block
  v2_no_calibration_scale — remove the 0.50-0.90 scale ladder
  v3_minimal              — just "return JSON probabilities + rationale"
  v4_meta_role            — prepend "You are a superforecaster..."

Usage:
    python scripts/ablate_prompt.py --variants v0,v1,v2
    python scripts/ablate_prompt.py --variants all

Cost: 26 events × $0.10 per variant. Default 4 variants = ~$10.
"""

from __future__ import annotations

import argparse
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

from forecast_track import (  # noqa: E402
    _brave_search,
    _build_query,
    _build_retrieval_user_prompt,
    _clamp,
    _dedupe_by_domain,
    _match_outcome_label,
    _parse_multi_outcome_json,
    apply_longshot_guard,
)

log = logging.getLogger("ablate_prompt")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

_MODEL = os.environ.get("PROPHET_ABLATE_PROMPT_MODEL", "claude-opus-4-7")
_ANTHROPIC = Anthropic()


# Production prompt (verbatim) — keep in sync with forecast_track.py's
# _MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT.
V0_PRODUCTION = """\
You are a calibrated probabilistic forecaster for prediction markets with
access to recent web evidence.

Your task: assign a probability to EACH listed outcome of the event using
both your prior knowledge and the supplied evidence snippets. Every outcome
must receive a probability; do not omit any. Probabilities do NOT need to
sum to 1 -- the scoring server normalizes them before grading.

Treat the evidence snippets as factual claims from third-party sources.
Do not fabricate URLs, dates, or details that are not present in the
snippets. If the evidence is stale or unrelated to the resolution
criterion, say so in the rationale and lean closer to the uninformed
prior.

Calibration scale (apply to each outcome independently):
  0.50 = no view; default for genuine uncertainty.
  0.60 = slight lean; weak base rate or partial evidence.
  0.70 = real view; concrete reasoning, multiple consistent signals.
  0.80 = strong view; hard evidence, clear mechanism.
  0.90 = near-certain; mechanically determined or authoritative source.

Market-odds anchoring (IMPORTANT):
If the evidence cites explicit market odds, implied probabilities, or
betting prices for any outcome (e.g. "+1500" implies ~6%, "-200" implies
~67%, "trading at 0.25" implies 25%), anchor your forecast for that
outcome strongly to that number. Markets aggregate informed money;
move more than 0.05 away from a cited market price only when you have
specific contrary evidence in the snippets (not vibes, not narratives).
LLMs systematically overweight vivid low-probability stories; resist that.

Rules:
- Output ONLY valid JSON of the shape:
    {"probabilities": {"<outcome label>": <float>, ...},
     "rationale": "<one-line summary>"}
- Use the EXACT outcome labels supplied in the prompt as keys.
- Each probability must be in [0.01, 0.99].
- Never emit 0.01 or 0.99 unless mechanically determined.
- The rationale is a single line summarizing your overall reasoning.
"""

V1_NO_ANCHOR = V0_PRODUCTION.replace(
    """Market-odds anchoring (IMPORTANT):
If the evidence cites explicit market odds, implied probabilities, or
betting prices for any outcome (e.g. "+1500" implies ~6%, "-200" implies
~67%, "trading at 0.25" implies 25%), anchor your forecast for that
outcome strongly to that number. Markets aggregate informed money;
move more than 0.05 away from a cited market price only when you have
specific contrary evidence in the snippets (not vibes, not narratives).
LLMs systematically overweight vivid low-probability stories; resist that.

""",
    "",
)

V2_NO_CALIBRATION_SCALE = V0_PRODUCTION.replace(
    """Calibration scale (apply to each outcome independently):
  0.50 = no view; default for genuine uncertainty.
  0.60 = slight lean; weak base rate or partial evidence.
  0.70 = real view; concrete reasoning, multiple consistent signals.
  0.80 = strong view; hard evidence, clear mechanism.
  0.90 = near-certain; mechanically determined or authoritative source.

""",
    "",
)

V3_MINIMAL = """\
For each event outcome supplied, return a probability in [0.01, 0.99].
Use the EXACT outcome labels as JSON keys.

Output ONLY JSON of the shape:
{"probabilities": {"<outcome label>": <float>, ...},
 "rationale": "<one-line summary>"}
"""

V4_META_ROLE = (
    "You are a superforecaster with a track record of well-calibrated "
    "probabilistic predictions on real-world events. You quantify "
    "uncertainty honestly and resist the LLM tendency to round confidence "
    "toward 0.5 or toward 1.0. You make every claim provenanced to the "
    "evidence in front of you.\n\n"
) + V0_PRODUCTION

PROMPT_VARIANTS = {
    "v0_production":           V0_PRODUCTION,
    "v1_no_anchor":            V1_NO_ANCHOR,
    "v2_no_calibration_scale": V2_NO_CALIBRATION_SCALE,
    "v3_minimal":              V3_MINIMAL,
    "v4_meta_role":            V4_META_ROLE,
}


def _predict_one(event: dict, system_prompt: str) -> dict:
    outs = event.get("outcomes") or []
    n = len(outs)
    if n == 0:
        return {"market_ticker": event.get("market_ticker"),
                "p_yes": 0.5, "probabilities": [], "rationale": "no outcomes"}

    # Retrieval (same as production)
    chunks: list[dict] = []
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        try:
            raw = _brave_search(_build_query(event), count=5)
            chunks = _dedupe_by_domain(raw)
        except Exception as e:
            log.warning("brave failed for %s: %s", event.get("market_ticker"), e)

    user = _build_retrieval_user_prompt(event, chunks)
    try:
        resp = _ANTHROPIC.messages.create(
            model=_MODEL, max_tokens=900,
            system=system_prompt,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text if resp.content else ""
        parsed = _parse_multi_outcome_json(text)
        raw_probs = parsed.get("probabilities") or {}
        if not isinstance(raw_probs, dict):
            raise ValueError("probabilities not a dict")
        prior = 1.0 / n
        resolved: dict[str, float] = {}
        for raw_key, raw_val in raw_probs.items():
            canon = _match_outcome_label(str(raw_key), outs)
            if canon and canon not in resolved:
                try:
                    resolved[canon] = _clamp(float(raw_val))
                except (TypeError, ValueError):
                    pass
        prob_list = [{"market": o, "probability": resolved.get(o, prior)} for o in outs]
        rationale = str(parsed.get("rationale", ""))[:300]
    except Exception as e:
        log.warning("llm failed for %s: %s", event.get("market_ticker"), e)
        prior = 1.0 / n
        prob_list = [{"market": o, "probability": prior} for o in outs]
        rationale = f"llm error: {e}"
    guarded = apply_longshot_guard(prob_list, n)
    return {
        "market_ticker": event.get("market_ticker"),
        "p_yes": guarded[0]["probability"] if guarded else 0.5,
        "probabilities": guarded,
        "rationale": rationale,
    }


def _multi_brier(probs: list[float], winner_idx: int) -> float:
    return sum((p - (1.0 if i == winner_idx else 0.0)) ** 2 for i, p in enumerate(probs))


def _run_variant(label: str, prompt: str, events: list[dict], workers: int) -> dict:
    print(f"\n=== variant {label}: {len(events)} events ===")
    t0 = time.time()
    preds = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_predict_one, e, prompt): e for e in events}
        for i, fut in enumerate(as_completed(futs), 1):
            preds.append(fut.result())
            if i % 5 == 0:
                print(f"  {i}/{len(events)} ({time.time()-t0:.1f}s)")
    print(f"  total: {time.time()-t0:.1f}s")

    # Score
    by_ticker_pred = {p["market_ticker"]: p for p in preds}
    bin_briers, multi_briers = [], []
    for e in events:
        t = e.get("market_ticker")
        if t not in by_ticker_pred:
            continue
        outs = e.get("outcomes") or []
        ro = e.get("resolved_outcome") or {}
        winner_list = ro.get("value") if isinstance(ro, dict) else None
        winner = winner_list[0] if winner_list else None
        if not outs or winner not in outs:
            continue
        wi = outs.index(winner)
        probs = [pp["probability"] for pp in by_ticker_pred[t]["probabilities"]]
        if len(outs) == 2:
            actual = 1 if wi == 0 else 0
            bin_briers.append((probs[0] - actual) ** 2)
        else:
            multi_briers.append(_multi_brier(probs, wi))
    all_briers = bin_briers + multi_briers
    mean = sum(all_briers) / len(all_briers) if all_briers else 0.0

    print(f"  Brier (n={len(all_briers)}):  mean = {mean:.4f}")
    if bin_briers:
        print(f"    binary  (n={len(bin_briers)}): {sum(bin_briers)/len(bin_briers):.4f}")
    if multi_briers:
        print(f"    multi   (n={len(multi_briers)}): {sum(multi_briers)/len(multi_briers):.4f}")

    return {
        "variant": label, "n": len(all_briers),
        "mean_brier": mean,
        "binary_mean": sum(bin_briers)/len(bin_briers) if bin_briers else None,
        "multi_mean":  sum(multi_briers)/len(multi_briers) if multi_briers else None,
        "predictions": preds,
        "wall_clock_s": time.time() - t0,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--variants", default="v0_production,v1_no_anchor,v4_meta_role",
                   help="comma-separated variant ids, or 'all'")
    p.add_argument("--events", default="data/resolved.json")
    p.add_argument("--workers", type=int, default=5)
    args = p.parse_args()

    chosen = (list(PROMPT_VARIANTS.keys()) if args.variants == "all"
              else args.variants.split(","))
    unknown = [v for v in chosen if v not in PROMPT_VARIANTS]
    if unknown:
        print(f"unknown variants: {unknown}; available: {list(PROMPT_VARIANTS)}", file=sys.stderr)
        return 1

    events = json.loads(Path(args.events).read_text())
    print(f"loaded {len(events)} events from {args.events}")
    print(f"running variants: {chosen} on model {_MODEL}")

    out_dir = Path("data/predictions")
    out_dir.mkdir(parents=True, exist_ok=True)
    all_results = []
    for v in chosen:
        result = _run_variant(v, PROMPT_VARIANTS[v], events, args.workers)
        all_results.append(result)
        (out_dir / f"prompt_ablation_{v}.json").write_text(
            json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "variant": v, "model": _MODEL,
                        "mean_brier": result["mean_brier"],
                        "binary_mean": result["binary_mean"],
                        "multi_mean":  result["multi_mean"],
                        "predictions": result["predictions"]},
                       indent=2),
        )

    # Comparison summary
    print("\n=== Summary ===")
    print(f"{'Variant':<28} {'Mean Brier':<12} {'Binary':<10} {'Multi':<10}")
    for r in all_results:
        bm = f"{r['binary_mean']:.4f}" if r["binary_mean"] is not None else "—"
        mm = f"{r['multi_mean']:.4f}"  if r["multi_mean"]  is not None else "—"
        print(f"  {r['variant']:<28} {r['mean_brier']:<12.4f} {bm:<10} {mm:<10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
