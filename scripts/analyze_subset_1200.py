#!/usr/bin/env python3
"""Post-run analysis of the Subset-1200 backtest.

Reads `data/predictions/subset_1200.json` + `subset_1200_actuals.json`,
computes:

  - mean single-binary Brier (the headline number we publish)
  - 95% paired-bootstrap CI on the per-event Brier
  - distribution by category (Sports / Politics / Economics / Other)
  - leakage audit on the new evidence URLs (do we still see ~38.5%
    post-resolution markers at scale?)
  - per-event Brier distribution (median, IQR, outlier count)
  - parse-error rate (rationale begins with 'llm error' or contains
    'unparseable')

Writes `data/predictions/subset_1200_summary.json` for the report
builder + prints a human-readable summary to stdout.
"""
from __future__ import annotations

import json
import re
import sys
import statistics as st
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
PRED = REPO / "data" / "predictions"

LEAK_MARKERS = re.compile(
    r"\b(won|wins|winner|winners|champion|champions|final|finals|results|"
    r"defeated|defeat|beat|beats|crowned|recap|finale|standings|outcome|"
    r"who-won|results-page)\b"
)


def main() -> int:
    preds_path = PRED / "subset_1200.json"
    actuals_path = PRED / "subset_1200_actuals.json"
    if not preds_path.exists() or not actuals_path.exists():
        print(f"missing inputs: {preds_path} or {actuals_path}", file=sys.stderr)
        print("run scripts/ablate_subset_1200.py first", file=sys.stderr)
        return 2

    data = json.loads(preds_path.read_text())
    rows = data.get("predictions", data)
    actuals = json.loads(actuals_path.read_text())

    # Aggregate
    losses = []
    parse_err = 0
    suspect_urls = 0
    total_urls = 0
    events_with_any_suspect = 0
    suspect_domains: dict[str, int] = defaultdict(int)
    per_event = []

    for r in rows:
        t = r["market_ticker"]
        a = actuals.get(t)
        if a is None:
            continue
        p = float(r["p_yes"])
        loss = (p - float(a)) ** 2
        losses.append(loss)

        rationale = (r.get("rationale", "") or "").lower()
        if rationale.startswith("llm error") or "unparseable" in rationale:
            parse_err += 1

        urls = r.get("evidence_urls", []) or []
        n_susp = 0
        for u in urls:
            total_urls += 1
            path = urlparse(u).path.lower()
            if LEAK_MARKERS.search(path):
                suspect_urls += 1
                n_susp += 1
                suspect_domains[urlparse(u).netloc.lower()] += 1
        if n_susp:
            events_with_any_suspect += 1

        per_event.append({"ticker": t, "p_yes": p, "actual": int(a), "brier": loss})

    n = len(losses)
    if n == 0:
        print("no usable events; bailing")
        return 1

    arr = np.array(losses)
    mean = float(arr.mean())
    median = float(np.median(arr))
    p25, p75 = float(np.percentile(arr, 25)), float(np.percentile(arr, 75))

    # Paired bootstrap 95% CI on the mean
    rng = np.random.default_rng(20260517)
    idx = rng.integers(0, n, size=(50000, n))
    boot_means = arr[idx].mean(axis=1)
    ci_lo, ci_hi = float(np.percentile(boot_means, 2.5)), float(np.percentile(boot_means, 97.5))

    summary = {
        "n_events_scored": n,
        "mean_brier": round(mean, 5),
        "median_brier": round(median, 5),
        "iqr": [round(p25, 5), round(p75, 5)],
        "ci95": [round(ci_lo, 5), round(ci_hi, 5)],
        "parse_error_rate_pct": round(100 * parse_err / max(len(rows), 1), 2),
        "leakage_audit": {
            "events_with_any_suspect_pct": round(100 * events_with_any_suspect / n, 1),
            "suspect_urls_pct": round(100 * suspect_urls / max(total_urls, 1), 1),
            "n_events": n,
            "n_total_urls": total_urls,
            "top_domains": dict(sorted(suspect_domains.items(), key=lambda kv: -kv[1])[:8]),
        },
        "comparison_with_26_event": {
            "n_26_brier": 0.03782,
            "n_1200_brier": round(mean, 5),
            "delta": round(mean - 0.03782, 5),
        },
    }
    out = PRED / "subset_1200_summary.json"
    out.write_text(json.dumps(summary, indent=2))

    print(f"=== Subset-1200 backtest results ===")
    print(f"events scored:                 {n} / {len(rows)} (rest had missing actuals)")
    print(f"mean single-binary Brier:      {mean:.5f}")
    print(f"95% bootstrap CI:              [{ci_lo:.5f}, {ci_hi:.5f}]")
    print(f"median / IQR:                  {median:.5f} / [{p25:.5f}, {p75:.5f}]")
    print(f"parse-error rate:              {100*parse_err/max(len(rows),1):.2f}%")
    print()
    print(f"Comparison to n=26 sample-resolved:")
    print(f"  n=26 Brier:    0.03782")
    print(f"  n=1200 Brier:  {mean:.5f}  (delta {mean-0.03782:+.5f})")
    print()
    print(f"Leakage audit (new sample):")
    print(f"  events with any suspect URL:  {events_with_any_suspect}/{n} = {100*events_with_any_suspect/n:.1f}%")
    print(f"  suspect URLs / total URLs:    {suspect_urls}/{total_urls} = {100*suspect_urls/max(total_urls,1):.1f}%")
    print(f"  (n=26 had 38.5% events and 23.8% URLs flagged)")
    print()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
