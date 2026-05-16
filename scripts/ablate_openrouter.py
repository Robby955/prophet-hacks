"""One-off ablation: run predict_multi_outcome_retrieval through an
OpenRouter-hosted model instead of Opus 4.7, then compare Brier on the
26-event sample-resolved set.

Default target: google/gemini-3.1-pro-preview (closest to "Gemini 3 Pro"
on the Prophet Arena fixed-context leaderboard).

Crucially, EVERY OTHER step is identical to predict_multi_outcome_retrieval:
same Brave query construction, same dedup, same evidence-injection
prompt, same longshot guard. Only the LLM call is swapped. That isolates
"is the model better?" from "is the pipeline different?"

Usage:
    python scripts/ablate_openrouter.py --model google/gemini-3.1-pro-preview
    python scripts/ablate_openrouter.py --model google/gemini-2.5-pro
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

import httpx  # noqa: E402

# Reuse the exact pipeline pieces so the ablation is honest.
from forecast_track import (  # noqa: E402
    _MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT,
    _brave_search,
    _build_query,
    _build_retrieval_user_prompt,
    _clamp,
    _dedupe_by_domain,
    _parse_multi_outcome_json,
    apply_longshot_guard,
    predict_multi_outcome,
)

log = logging.getLogger("ablate")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")


def _openrouter_call(model: str, system: str, user: str, max_tokens: int = 900) -> str:
    key = os.environ["OPENROUTER_API_KEY"]
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://forecastingpath.com",
            "X-Title": "ForecastingPath ablation",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=60.0,
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def predict_one(event: dict, model: str) -> dict:
    """Same shape as predict_multi_outcome_retrieval, LLM swapped."""
    outs = event.get("outcomes") or []
    n = len(outs)
    if n == 0:
        return {"p_yes": 0.5, "rationale": "no outcomes", "probabilities": [], "evidence_urls": []}

    # Retrieval (same as production)
    chunks: list[dict] = []
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        query = _build_query(event)
        try:
            raw = _brave_search(query, count=5)
            chunks = _dedupe_by_domain(raw)
        except Exception as e:
            log.warning("Brave failed for %s: %s", event.get("market_ticker", "?"), e)
            chunks = []

    evidence_urls = [c["url"] for c in chunks if c.get("url")]
    user = _build_retrieval_user_prompt(event, chunks)

    # LLM call (the ONE thing we're ablating)
    try:
        text = _openrouter_call(model, _MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT, user)
        parsed = _parse_multi_outcome_json(text)
        raw_probs = parsed.get("probabilities") or {}
        if not isinstance(raw_probs, dict):
            raise ValueError(f"probabilities not a dict: {type(raw_probs).__name__}")
        prior = 1.0 / n
        prob_list: list[dict] = []
        for o in outs:
            v = raw_probs.get(o)
            try:
                p = _clamp(float(v)) if v is not None else prior
            except (TypeError, ValueError):
                p = prior
            prob_list.append({"market": o, "probability": p})
        rationale = str(parsed.get("rationale", ""))[:300]
    except Exception as e:
        log.warning("%s LLM failed for %s: %s; uniform fallback",
                    model, event.get("market_ticker", "?"), e)
        prior = 1.0 / n
        prob_list = [{"market": o, "probability": prior} for o in outs]
        rationale = f"llm error: {e}"

    guarded = apply_longshot_guard(prob_list, n)
    return {
        "market_ticker": event.get("market_ticker"),
        "p_yes": guarded[0]["probability"] if guarded else 0.5,
        "rationale": rationale,
        "probabilities": guarded,
        "evidence_urls": evidence_urls,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="google/gemini-3.1-pro-preview")
    p.add_argument("--events", default="data/resolved.json")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out-dir", default="data/predictions")
    p.add_argument("--label", default="", help="filename suffix; defaults to model basename")
    args = p.parse_args()

    with open(args.events) as f:
        events = json.load(f)
    if isinstance(events, dict):
        events = list(events.values())

    print(f"=== ablation: {args.model} on {len(events)} events ===")
    t0 = time.time()
    preds: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(predict_one, e, args.model): e for e in events}
        for i, fut in enumerate(as_completed(futs), 1):
            ev = futs[fut]
            try:
                pred = fut.result()
                preds.append(pred)
            except Exception as e:
                log.error("predict failed for %s: %s", ev.get("market_ticker", "?"), e)
            if i % 5 == 0:
                print(f"  {i}/{len(events)} done ({time.time()-t0:.1f}s)")
    print(f"  total: {time.time()-t0:.1f}s")

    label = args.label or args.model.split("/")[-1].replace(".", "-")
    out_path = Path(args.out_dir) / f"ablation_{label}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "model": args.model,
                                    "predictions": preds}, indent=2))
    print(f"  wrote {out_path}")

    # Brier vs resolved
    with open(args.events) as f:
        evs = json.load(f)
    if isinstance(evs, dict):
        evs = list(evs.values())
    by_ticker = {e["market_ticker"]: e for e in evs}
    pd = {p["market_ticker"]: p for p in preds}
    binary_briers = []
    multi_briers = []
    n_total = 0
    for tk, ev in by_ticker.items():
        if tk not in pd:
            continue
        outs = ev.get("outcomes") or []
        ro = ev.get("resolved_outcome") or {}
        winner_list = ro.get("value") if isinstance(ro, dict) else None
        winner = winner_list[0] if winner_list else None
        if not outs or winner not in outs:
            continue
        n_total += 1
        wi = outs.index(winner)
        probs = [x.get("probability", 0.0) for x in pd[tk].get("probabilities", [])]
        if len(outs) == 2:
            # Binary Brier on outcome[0] = p_yes
            actual = 1 if wi == 0 else 0
            binary_briers.append((probs[0] - actual) ** 2)
        else:
            multi_briers.append(sum(
                (p - (1.0 if i == wi else 0.0)) ** 2 for i, p in enumerate(probs)
            ))
    all_briers = binary_briers + multi_briers
    mean = sum(all_briers) / len(all_briers) if all_briers else 0.0
    print()
    print(f"=== Brier on {n_total} resolved events ===")
    print(f"  mean Brier:        {mean:.4f}")
    if binary_briers:
        print(f"  binary mean (n={len(binary_briers)}): "
              f"{sum(binary_briers)/len(binary_briers):.4f}")
    if multi_briers:
        print(f"  multi  mean (n={len(multi_briers)}): "
              f"{sum(multi_briers)/len(multi_briers):.4f}")
    print()
    print(f"=== Compare to current production (Phase 2: Opus 4.7 + anchor + 0.10 floor) ===")
    print(f"  Phase 2 mean Brier:  0.0379")
    print(f"  Ablation mean Brier: {mean:.4f}")
    diff = mean - 0.0379
    print(f"  Δ = {diff:+.4f}  ({100*diff/0.0379:+.1f}% relative)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
