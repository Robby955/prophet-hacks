"""Search-provider bake-off: hold the production model/prompt/guard constant
and swap ONLY the retrieval source.

Every prior ablation swapped the LLM and held Brave constant. This one does the
opposite: same Opus 4.7, same `_MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT`, same
dedupe-by-domain and longshot guard — only the search call changes. That
isolates "is the search provider better?" from "is the pipeline different?".

Providers:
  brave        control: Brave Web Search, count=5 (current production retrieval)
  brave_fresh  Brave restricted to results dated BEFORE each event's close_time
               (leakage-discipline arm — directly tests the 38.5% post-resolution
               leakage finding from check_retrieval_leakage.py)
  tavily       Tavily Search API        (needs TAVILY_API_KEY in env)
  exa          Exa /search + contents   (needs EXA_API_KEY in env)
  serper       Serper Google SERP proxy (needs SERPER_API_KEY in env)

Output is a standard prediction file per provider at
`data/predictions/ablation_search_<provider>.json`, shaped exactly like every
other variant ({"predictions": [{market_ticker, p_yes, probabilities,
evidence_urls}]}), so it feeds straight into:
    python scripts/bootstrap_brier_ci.py \
        --model data/predictions/ablation_search_brave_fresh.json \
        --baseline data/predictions/ablation_search_brave.json \
        --actuals data/actuals.json --model-label brave_fresh --baseline-label brave

Usage:
    python scripts/ablate_search_provider.py --providers brave,brave_fresh
    python scripts/ablate_search_provider.py --providers brave,tavily,exa,serper
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import httpx  # noqa: E402

from forecast_track import (  # noqa: E402
    _MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT,
    _OPUS_MODEL,
    _brave_search,
    _build_query,
    _build_retrieval_user_prompt,
    _call_anthropic,
    _clamp,
    _dedupe_by_domain,
    _extract_domain,
    _parse_multi_outcome_json,
    apply_longshot_guard,
)

log = logging.getLogger("ablate_search")
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

# Reuse the leakage markers from check_retrieval_leakage.py so the leakage
# column here matches the audit elsewhere.
LEAK_MARKERS = re.compile(
    r"\b(won|wins|winner|winners|champion|champions|final|finals|results|"
    r"defeated|defeat|beat|beats|crowned|recap|finale|standings|"
    r"who-won|outcome|results-page)\b"
)


def _is_suspect(url: str) -> bool:
    return bool(LEAK_MARKERS.search(urlparse(url).path.lower()))


def _norm(results: list[dict]) -> list[dict]:
    """Coerce any provider's rows into {title,url,snippet,domain}."""
    out: list[dict] = []
    for r in results:
        url = str(r.get("url") or "")
        if not url:
            continue
        out.append(
            {
                "title": str(r.get("title") or "")[:200],
                "url": url,
                "snippet": str(r.get("snippet") or r.get("description") or r.get("content") or "")[:400],
                "domain": _extract_domain(url),
            }
        )
    return out


# --------------------------------------------------------------------------
# providers — each returns raw rows; dedupe-by-domain is applied uniformly after
# --------------------------------------------------------------------------
def _cutoff_freshness(event: dict) -> str | None:
    """Brave freshness range ending the day before the event closed."""
    ct = event.get("close_time")
    if not ct:
        return None
    try:
        end = datetime.fromisoformat(str(ct).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    end = (end - timedelta(days=1)).date()
    start = end - timedelta(days=120)
    return f"{start.isoformat()}to{end.isoformat()}"


def search_brave(event: dict, query: str) -> list[dict]:
    return _norm(_brave_search(query, count=5))


def search_brave_fresh(event: dict, query: str) -> list[dict]:
    freshness = _cutoff_freshness(event)
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not api_key:
        raise RuntimeError("BRAVE_SEARCH_API_KEY not set")
    params = {"q": query, "count": 5}
    if freshness:
        params["freshness"] = freshness
    r = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params=params,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        timeout=10.0,
    )
    r.raise_for_status()
    raw = ((r.json() or {}).get("web") or {}).get("results") or []
    return _norm(
        [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("description")} for x in raw]
    )


def search_tavily(event: dict, query: str) -> list[dict]:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY not set")
    r = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": 5, "search_depth": "basic"},
        timeout=20.0,
    )
    r.raise_for_status()
    return _norm(r.json().get("results") or [])


def search_exa(event: dict, query: str) -> list[dict]:
    key = os.environ.get("EXA_API_KEY")
    if not key:
        raise RuntimeError("EXA_API_KEY not set")
    r = httpx.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": key, "Content-Type": "application/json"},
        json={"query": query, "numResults": 5, "contents": {"text": {"maxCharacters": 400}}},
        timeout=20.0,
    )
    r.raise_for_status()
    rows = []
    for x in r.json().get("results") or []:
        rows.append({"title": x.get("title"), "url": x.get("url"), "snippet": x.get("text")})
    return _norm(rows)


def search_serper(event: dict, query: str) -> list[dict]:
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        raise RuntimeError("SERPER_API_KEY not set")
    r = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": 5},
        timeout=20.0,
    )
    r.raise_for_status()
    rows = []
    for x in r.json().get("organic") or []:
        rows.append({"title": x.get("title"), "url": x.get("link"), "snippet": x.get("snippet")})
    return _norm(rows)


PROVIDERS = {
    "brave": search_brave,
    "brave_fresh": search_brave_fresh,
    "tavily": search_tavily,
    "exa": search_exa,
    "serper": search_serper,
}


# --------------------------------------------------------------------------
# prediction — identical to production except for the injected search_fn
# --------------------------------------------------------------------------
def predict_one(event: dict, search_fn) -> dict:
    outs = event.get("outcomes") or []
    n = len(outs)
    if n == 0:
        return {"market_ticker": event.get("market_ticker"), "p_yes": 0.5,
                "rationale": "no outcomes", "probabilities": [], "evidence_urls": []}

    query = _build_query(event)
    try:
        chunks = _dedupe_by_domain(search_fn(event, query))
    except Exception as e:  # noqa: BLE001 — provider errors must not abort the run
        log.warning("search failed for %s: %s", event.get("market_ticker", "?"), e)
        chunks = []
    evidence_urls = [c["url"] for c in chunks if c.get("url")]

    user = _build_retrieval_user_prompt(event, chunks)
    try:
        text = _call_anthropic(_OPUS_MODEL, _MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT, user)
        parsed = _parse_multi_outcome_json(text)
        raw_probs = parsed.get("probabilities") or {}
        if not isinstance(raw_probs, dict):
            raise ValueError(f"probabilities not a dict: {type(raw_probs).__name__}")
        prior = 1.0 / n
        prob_list = []
        for o in outs:
            v = raw_probs.get(o)
            try:
                p = _clamp(float(v)) if v is not None else prior
            except (TypeError, ValueError):
                p = prior
            prob_list.append({"market": o, "probability": p})
        rationale = str(parsed.get("rationale", ""))[:300]
    except Exception as e:  # noqa: BLE001
        log.warning("LLM failed for %s: %s; uniform fallback", event.get("market_ticker", "?"), e)
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


def run_provider(provider: str, events: list[dict], workers: int, out_dir: Path) -> dict:
    search_fn = PROVIDERS[provider]
    print(f"\n=== provider: {provider} on {len(events)} events ===")
    t0 = time.time()
    preds: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(predict_one, e, search_fn): e for e in events}
        for i, fut in enumerate(as_completed(futs), 1):
            ev = futs[fut]
            try:
                preds.append(fut.result())
            except Exception as e:  # noqa: BLE001
                log.error("predict failed for %s: %s", ev.get("market_ticker", "?"), e)
            if i % 5 == 0:
                print(f"  {i}/{len(events)} done ({time.time()-t0:.1f}s)")
    elapsed = time.time() - t0

    out_path = out_dir / f"ablation_search_{provider}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "provider": provider, "model": _OPUS_MODEL, "predictions": preds}, indent=2))

    total_urls = sum(len(p.get("evidence_urls", [])) for p in preds)
    suspect = sum(_is_suspect(u) for p in preds for u in p.get("evidence_urls", []))
    empty = sum(1 for p in preds if not p.get("evidence_urls"))
    print(f"  wrote {out_path}  ({elapsed:.1f}s)")
    return {
        "provider": provider,
        "path": str(out_path),
        "n_preds": len(preds),
        "evidence_urls": total_urls,
        "suspect_urls": suspect,
        "leakage_pct": round(100 * suspect / total_urls, 1) if total_urls else 0.0,
        "events_no_evidence": empty,
        "seconds": round(elapsed, 1),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--providers", default="brave,brave_fresh",
                   help="comma-separated subset of: " + ",".join(PROVIDERS))
    p.add_argument("--events", default="data/resolved.json")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out-dir", default="data/predictions")
    args = p.parse_args()

    requested = [s.strip() for s in args.providers.split(",") if s.strip()]
    unknown = [p_ for p_ in requested if p_ not in PROVIDERS]
    if unknown:
        print(f"unknown provider(s): {unknown}; valid: {list(PROVIDERS)}", file=sys.stderr)
        return 2

    events = json.loads(Path(args.events).read_text())
    if isinstance(events, dict):
        events = list(events.values())

    summaries = []
    for provider in requested:
        # Pre-flight key check so we fail loud, not with 26 uniform fallbacks.
        env_key = {"tavily": "TAVILY_API_KEY", "exa": "EXA_API_KEY", "serper": "SERPER_API_KEY"}.get(provider)
        if env_key and not os.environ.get(env_key):
            print(f"\n=== provider: {provider} SKIPPED — {env_key} not in env "
                  f"(add it to ~/Desktop/variables.txt) ===")
            continue
        summaries.append(run_provider(provider, events, args.workers, Path(args.out_dir)))

    print("\n=== retrieval summary ===")
    print(f"{'provider':<14}{'preds':>6}{'urls':>6}{'leak%':>7}{'no-ev':>7}{'secs':>7}")
    for s in summaries:
        print(f"{s['provider']:<14}{s['n_preds']:>6}{s['evidence_urls']:>6}"
              f"{s['leakage_pct']:>7}{s['events_no_evidence']:>7}{s['seconds']:>7}")

    if len(summaries) >= 2:
        base = summaries[0]["provider"]
        print(f"\nnext: paired bootstrap CI vs control '{base}':")
        for s in summaries[1:]:
            print(f"  python scripts/bootstrap_brier_ci.py "
                  f"--model {s['path']} --baseline {summaries[0]['path']} "
                  f"--actuals data/actuals.json "
                  f"--model-label {s['provider']} --baseline-label {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
