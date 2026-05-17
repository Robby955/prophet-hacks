#!/usr/bin/env python3
"""E5: backtest leakage audit.

Scans evidence URLs in our cached predictions for post-resolution
content markers — telltale phrases like "won", "winner", "champion",
"results" in URL paths suggest the article was indexed AFTER the
event resolved, which means Brave Search returned hindsight-laced
results during our backtest.

Run:
    python scripts/check_retrieval_leakage.py
prints a per-event suspect count + dataset-level summary.
Writes data/predictions/leakage_audit.json for the report builder.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"

# Post-resolution markers in URL paths.
LEAK_MARKERS = re.compile(
    r"\b(won|wins|winner|winners|champion|champions|final|finals|results|"
    r"defeated|defeat|beat|beats|crowned|recap|finale|standings|"
    r"who-won|outcome|results-page)\b"
)


def is_suspect(url: str) -> bool:
    path = urlparse(url).path.lower()
    return bool(LEAK_MARKERS.search(path))


def main() -> int:
    events = {e["market_ticker"]: e for e in json.load(open(DATA / "resolved.json"))}
    prod = json.load(open(PRED / "multi_outcome_retrieval.json"))
    rows = prod.get("predictions", prod)

    per_event: dict[str, dict] = {}
    domain_hits: Counter = Counter()
    total_urls = 0
    suspect_urls = 0
    events_with_any_suspect = 0

    for r in rows:
        t = r["market_ticker"]
        ev = events.get(t, {})
        ro = ev.get("resolved_outcome", {})
        resolved_at = ro.get("resolved_at") if isinstance(ro, dict) else None

        urls = r.get("evidence_urls", []) or []
        n_total = len(urls)
        suspects = [u for u in urls if is_suspect(u)]
        n_susp = len(suspects)

        total_urls += n_total
        suspect_urls += n_susp
        if n_susp:
            events_with_any_suspect += 1
            for u in suspects:
                domain_hits[urlparse(u).netloc.lower()] += 1

        per_event[t] = {
            "title": ev.get("title", "")[:80],
            "resolved_at": resolved_at[:10] if resolved_at else None,
            "n_evidence_urls": n_total,
            "n_suspect_urls": n_susp,
            "suspect_examples": [u[:140] for u in suspects[:3]],
        }

    print(f"=== Backtest leakage audit ===")
    print(f"Total events analyzed: {len(rows)}")
    print(f"Events with at least one suspect URL: {events_with_any_suspect} ({100*events_with_any_suspect/len(rows):.0f}%)")
    print(f"Total evidence URLs: {total_urls}")
    print(f"Suspect evidence URLs: {suspect_urls} ({100*suspect_urls/max(total_urls,1):.0f}%)")
    print()
    print("Top suspect domains:")
    for dom, n in domain_hits.most_common(8):
        print(f"  {n:>3} {dom}")
    print()
    print("Events with the most suspect URLs (likely most hindsight-contaminated):")
    worst = sorted(per_event.items(), key=lambda kv: -kv[1]["n_suspect_urls"])[:8]
    for t, info in worst:
        if info["n_suspect_urls"] == 0:
            break
        print(f"  [{info['n_suspect_urls']}/{info['n_evidence_urls']}] {t}")
        print(f"    resolved: {info['resolved_at']} · {info['title']}")

    summary = {
        "n_events": len(rows),
        "events_with_any_suspect": events_with_any_suspect,
        "events_with_any_suspect_pct": round(100 * events_with_any_suspect / max(len(rows), 1), 1),
        "total_urls": total_urls,
        "suspect_urls": suspect_urls,
        "suspect_urls_pct": round(100 * suspect_urls / max(total_urls, 1), 1),
        "top_suspect_domains": [{"domain": d, "n": n} for d, n in domain_hits.most_common(15)],
        "per_event": per_event,
    }
    out = PRED / "leakage_audit.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
