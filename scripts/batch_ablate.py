"""Offline ablation via Anthropic's Message Batches API.

The Batch API gives 50% discount + up to 24h turnaround vs the synchronous
Messages API. Useful for "swap-the-LLM" ablations across many events
where we can wait overnight.

Phase 1 (synchronous, ~1s/event): Brave search + dedupe per event.
Phase 2 (batched, queued): submit all LLM calls in one batch.
Phase 3 (poll, up to 24h): wait for `processing_status: ended`.
Phase 4 (synchronous): fetch results, parse, score.

Designed for `predict_multi_outcome_retrieval`'s prompt shape; works
with any Anthropic-hosted model. For OpenRouter/non-Anthropic models,
use scripts/ablate_openrouter.py (no batch discount, but supports any vendor).

Usage:
    # Submit a batch:
    python scripts/batch_ablate.py submit \\
        --events data/resolved.json \\
        --model claude-opus-4-7 \\
        --label opus47-batch-2026-05-16

    # Poll status:
    python scripts/batch_ablate.py status --label opus47-batch-2026-05-16

    # Fetch + score once ended:
    python scripts/batch_ablate.py fetch \\
        --label opus47-batch-2026-05-16 \\
        --actuals data/actuals.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import os  # noqa: E402

import httpx  # noqa: E402

from forecast_track import (  # noqa: E402
    _MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT,
    _brave_search,
    _build_query,
    _build_retrieval_user_prompt,
    _clamp,
    _dedupe_by_domain,
    _match_outcome_label,
    _parse_multi_outcome_json,
    apply_longshot_guard,
)


ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "data/batches"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _api_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if not k:
        raise SystemExit("ANTHROPIC_API_KEY not set in env")
    return k


def _headers() -> dict[str, str]:
    return {
        "x-api-key": _api_key(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _state_path(label: str) -> Path:
    return STATE_DIR / f"{label}.json"


# -- submit ----------------------------------------------------------------


def _retrieve_evidence(events: list[dict], workers: int) -> dict[str, list[dict]]:
    """Run Brave for every event in parallel. Returns ticker -> list of chunks."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out: dict[str, list[dict]] = {}

    def one(e: dict) -> tuple[str, list[dict]]:
        t = e.get("market_ticker", "?")
        query = _build_query(e)
        try:
            raw = _brave_search(query, count=5)
            return t, _dedupe_by_domain(raw)
        except Exception as ex:
            print(f"  brave failed for {t}: {ex}", file=sys.stderr)
            return t, []

    print(f"phase 1: Brave for {len(events)} events ({workers} workers)")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, e) for e in events]
        for i, f in enumerate(as_completed(futs), 1):
            t, chunks = f.result()
            out[t] = chunks
            if i % 10 == 0:
                print(f"  {i}/{len(events)} ({time.time()-t0:.1f}s)")
    print(f"  done in {time.time()-t0:.1f}s")
    return out


def cmd_submit(args: argparse.Namespace) -> int:
    events_path = Path(args.events)
    raw = json.loads(events_path.read_text())
    events = raw if isinstance(raw, list) else raw.get("events", [])
    if args.limit:
        events = events[: args.limit]
    print(f"loaded {len(events)} events from {events_path}")

    # Phase 1: synchronous Brave
    evidence = _retrieve_evidence(events, args.workers)

    # Phase 2: build batch requests
    requests = []
    custom_id_to_event = {}
    for i, e in enumerate(events):
        ticker = e.get("market_ticker") or f"evt-{i}"
        chunks = evidence.get(ticker, [])
        outs = e.get("outcomes") or []
        if not outs:
            continue
        user = _build_retrieval_user_prompt(e, chunks)
        custom_id = f"{i:04d}-{ticker[:36]}"
        custom_id_to_event[custom_id] = {
            "event": {k: e.get(k) for k in ["event_ticker","market_ticker","title","category","outcomes","resolved_outcome"]},
            "chunks": chunks,
        }
        requests.append({
            "custom_id": custom_id,
            "params": {
                "model": args.model,
                "max_tokens": 900,
                "system": _MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user}],
            },
        })

    print(f"phase 2: submit batch ({len(requests)} requests, model={args.model})")
    r = httpx.post(
        "https://api.anthropic.com/v1/messages/batches",
        headers=_headers(),
        json={"requests": requests},
        timeout=120,
    )
    if r.status_code >= 400:
        print(f"submit failed: {r.status_code} {r.text[:500]}", file=sys.stderr)
        return 1
    batch = r.json()
    state = {
        "label": args.label,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "batch_id": batch["id"],
        "model": args.model,
        "request_count": len(requests),
        "custom_id_to_event": custom_id_to_event,
    }
    _state_path(args.label).write_text(json.dumps(state, indent=2))
    print(f"  batch_id={batch['id']}")
    print(f"  status_url=https://api.anthropic.com/v1/messages/batches/{batch['id']}")
    print(f"  saved state to {_state_path(args.label)}")
    print(f"\nPoll with: python {sys.argv[0]} status --label {args.label}")
    return 0


# -- status ----------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    state = json.loads(_state_path(args.label).read_text())
    r = httpx.get(
        f"https://api.anthropic.com/v1/messages/batches/{state['batch_id']}",
        headers=_headers(), timeout=30,
    )
    r.raise_for_status()
    b = r.json()
    print(f"batch {b['id']}: processing_status={b.get('processing_status')}")
    counts = b.get("request_counts", {})
    for k, v in counts.items():
        print(f"  {k}: {v}")
    print(f"  created: {b.get('created_at')}")
    print(f"  expires: {b.get('expires_at')}")
    if b.get("processing_status") == "ended":
        print(f"\nfetch results with: python {sys.argv[0]} fetch --label {args.label}")
    return 0


# -- fetch + score ---------------------------------------------------------


def cmd_fetch(args: argparse.Namespace) -> int:
    state = json.loads(_state_path(args.label).read_text())
    custom_id_to_event = state["custom_id_to_event"]
    batch_id = state["batch_id"]

    r = httpx.get(
        f"https://api.anthropic.com/v1/messages/batches/{batch_id}",
        headers=_headers(), timeout=30,
    )
    r.raise_for_status()
    b = r.json()
    if b.get("processing_status") != "ended":
        print(f"batch not ended yet (status={b.get('processing_status')}). "
              f"Run `status` to check.", file=sys.stderr)
        return 1

    results_url = b.get("results_url")
    if not results_url:
        print(f"no results_url on batch — unusual. body: {json.dumps(b)[:300]}", file=sys.stderr)
        return 1

    print(f"fetching results from {results_url}")
    r2 = httpx.get(results_url, headers=_headers(), timeout=120)
    r2.raise_for_status()

    # Results stream is JSONL (one row per request)
    rows = []
    for line in r2.text.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    print(f"  got {len(rows)} result rows")

    predictions = []
    for row in rows:
        cid = row.get("custom_id")
        ctx = custom_id_to_event.get(cid, {})
        ev = ctx.get("event", {})
        outs = ev.get("outcomes") or []
        n = len(outs)
        if n == 0:
            continue
        prior = 1.0 / n
        if row.get("result", {}).get("type") != "succeeded":
            preds = [{"market": o, "probability": prior} for o in outs]
            rationale = f"batch result type {row.get('result', {}).get('type')}"
        else:
            msg = row["result"]["message"]
            text = "".join(c.get("text", "") for c in msg.get("content", []) if c.get("type") == "text")
            try:
                parsed = _parse_multi_outcome_json(text)
                raw_probs = parsed.get("probabilities") or {}
                resolved = {}
                for raw_key, raw_val in raw_probs.items():
                    canon = _match_outcome_label(str(raw_key), outs)
                    if canon and canon not in resolved:
                        try:
                            resolved[canon] = _clamp(float(raw_val))
                        except (TypeError, ValueError):
                            pass
                preds = [{"market": o, "probability": resolved.get(o, prior)} for o in outs]
                rationale = str(parsed.get("rationale", ""))[:300]
            except Exception as e:
                preds = [{"market": o, "probability": prior} for o in outs]
                rationale = f"parse failed: {e}"

        guarded = apply_longshot_guard(preds, n)
        predictions.append({
            "market_ticker": ev.get("market_ticker"),
            "p_yes": guarded[0]["probability"] if guarded else 0.5,
            "rationale": rationale,
            "probabilities": guarded,
        })

    out_path = ROOT / "data/predictions" / f"batch_{args.label}.json"
    out_path.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": state["model"],
        "batch_id": batch_id,
        "predictions": predictions,
    }, indent=2))
    print(f"  wrote {out_path}")

    # Optionally score against actuals
    if args.actuals:
        actuals = json.loads(Path(args.actuals).read_text())
        binary_briers = []
        for p in predictions:
            t = p["market_ticker"]
            a = actuals.get(t)
            if a is None:
                continue
            actual_bit = 1 if (isinstance(a, (int, float)) and a >= 0.5) else 0
            if isinstance(a, dict):
                # actuals.json shape: {ticker: 1.0 | 0.0} from build_actuals.py
                continue
            binary_briers.append((p["p_yes"] - actual_bit) ** 2)
        if binary_briers:
            mean = sum(binary_briers) / len(binary_briers)
            print(f"\n  scored {len(binary_briers)} binary events: mean Brier {mean:.4f}")
    return 0


# -- entrypoint ------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("submit")
    s1.add_argument("--events", required=True)
    s1.add_argument("--model", default="claude-opus-4-7")
    s1.add_argument("--label", required=True, help="local name for this batch")
    s1.add_argument("--workers", type=int, default=5)
    s1.add_argument("--limit", type=int, default=None,
                    help="optionally cap to first N events for testing")
    s1.set_defaults(func=cmd_submit)

    s2 = sub.add_parser("status")
    s2.add_argument("--label", required=True)
    s2.set_defaults(func=cmd_status)

    s3 = sub.add_parser("fetch")
    s3.add_argument("--label", required=True)
    s3.add_argument("--actuals", default=None,
                    help="optionally score against actuals.json")
    s3.set_defaults(func=cmd_fetch)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
