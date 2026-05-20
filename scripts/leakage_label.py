#!/usr/bin/env python3
"""Leakage labeling for retrieval-augmented forecasting.

A core methodology contribution of this project: data leakage in
retrieval-augmented forecasting flows through TWO distinct channels, and
we treat detecting both as a first-class, reusable labeling step.

  (1) RETRIEVAL leakage -- the search engine returned articles that were
      published / indexed AFTER the event resolved (hindsight). Evidence
      that did not exist at forecast time cannot legitimately inform a
      forecast.

  (2) PARAMETRIC leakage -- the LLM itself (Opus 4.7, knowledge cutoff
      ~2026-01-01) may have been trained on the outcome. Events that
      resolved on/before the cutoff are at risk of being memorized;
      events that resolve after the cutoff cannot have been.

This script labels each prediction for both channels and reports per-file
leakage rates so freshness-filtered ("fresh") retrieval can be compared
against unfiltered retrieval.

RETRIEVAL CHANNEL -- two methods:

  Method A (marker heuristic, what we already have): reuse the
  post-resolution URL-path marker regex from
  scripts/check_retrieval_leakage.py (markers like won|winner|champion|
  results|recap|final|standings|...). A suspect URL is one whose path
  contains such a marker -- a cheap signal that the page is an
  outcome/recap article.

  Method B (datestamp heuristic, the rigorous upgrade -- best-effort
  OFFLINE): try to extract a publish date from each evidence URL's path
  (patterns like /2026/05/19/, -20260519-, /2026-05-19/). If a date is
  found AND it is strictly AFTER the event's resolution date, that is
  DEFINITIVE post-resolution leakage -- the article literally could not
  have existed at forecast time.

  NOTE ON RIGOR: the fully-rigorous version of Method B would FETCH each
  page and read its published-date metadata (HTTP Last-Modified, Open
  Graph `article:published_time`, JSON-LD `datePublished`, etc.). That
  requires network access and is out of scope for this offline labeler.
  The URL-path datestamp here is a cheap, conservative PROXY: it only
  fires when a date is embedded in the path, so it under-counts (many
  leaked URLs carry no path date) but never invents one. Treat its rate
  as a lower bound on true datestamp-confirmed retrieval leakage.

PARAMETRIC CHANNEL:
  parametric_clean = (event resolution date > 2026-01-01)
  Events resolving after the model cutoff cannot have been memorized;
  events resolving on/before it are flagged parametric_suspect.

INPUTS:
  data/predictions/ablation_search_brave.json
  data/predictions/ablation_search_brave_fresh.json
    rows: {market_ticker, evidence_urls:[...], ...}
  data/resolved.json
    events: {market_ticker, close_time, resolved_outcome.resolved_at, ...}
  Joined by market_ticker.

OUTPUT:
  reports/leakage_labels.json -- per-prediction labels + a summary block
  for BOTH files, plus a console summary table comparing brave vs
  brave_fresh leakage rates by channel.

Run:
    .venv/bin/python scripts/leakage_label.py

LOCAL ONLY. Creates only this script + reports/leakage_labels.json at
runtime. Does not modify any existing file.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"
REPORTS = REPO / "reports"

PRED_FILES = {
    "brave": PRED / "ablation_search_brave.json",
    "brave_fresh": PRED / "ablation_search_brave_fresh.json",
}

# Model knowledge cutoff: events resolving on/before this date are at
# risk of parametric (training-data) leakage.
MODEL_CUTOFF = date(2026, 1, 1)

# --- Retrieval channel, Method A: post-resolution URL-path markers. ---
# Reused verbatim from scripts/check_retrieval_leakage.py so the two
# audits agree by construction.
LEAK_MARKERS = re.compile(
    r"\b(won|wins|winner|winners|champion|champions|final|finals|results|"
    r"defeated|defeat|beat|beats|crowned|recap|finale|standings|"
    r"who-won|outcome|results-page)\b"
)

# --- Retrieval channel, Method B: publish date embedded in URL path. ---
# Conservative offline proxy for true page-published-date metadata.
DATESTAMP_PATTERNS = [
    re.compile(r"/(20\d{2})/(\d{2})/(\d{2})/"),        # /2026/05/19/
    re.compile(r"[-_](20\d{2})(\d{2})(\d{2})(?:[-_/]|$)"),  # -20260519-
    re.compile(r"/(20\d{2})-(\d{2})-(\d{2})(?:[-_/]|$)"),   # /2026-05-19/
]


def is_marker_suspect(url: str) -> bool:
    """Method A: does the URL path contain a post-resolution marker word?"""
    path = urlparse(url).path.lower()
    return bool(LEAK_MARKERS.search(path))


def url_path_date(url: str) -> date | None:
    """Method B: extract a publish date embedded in the URL path, if any.

    Returns the parsed date, or None if no recognizable datestamp is
    present (or the captured digits are not a valid calendar date).
    """
    path = urlparse(url).path.lower()
    for pat in DATESTAMP_PATTERNS:
        m = pat.search(path)
        if not m:
            continue
        y, mo, d = (int(g) for g in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            continue
    return None


def _parse_iso_date(value: str | None) -> date | None:
    """Parse the date portion of an ISO timestamp like 2026-05-13T17:02:27Z."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        # Fall back to the leading YYYY-MM-DD if present.
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def event_resolution_date(ev: dict) -> date | None:
    """Best available resolution date: resolved_at, else close_time."""
    ro = ev.get("resolved_outcome")
    resolved_at = ro.get("resolved_at") if isinstance(ro, dict) else None
    return _parse_iso_date(resolved_at) or _parse_iso_date(ev.get("close_time"))


def load_rows(path: Path) -> list[dict]:
    d = json.load(open(path))
    return d.get("predictions", d) if isinstance(d, dict) else d


def label_file(rows: list[dict], events: dict[str, dict]) -> dict:
    """Label every prediction in one file for both leakage channels."""
    per_prediction = []
    total_urls = 0
    total_marker_suspect = 0
    total_datestamp_after = 0
    n_parametric_suspect = 0
    n_events_with_marker = 0
    n_events_with_datestamp = 0

    for r in rows:
        ticker = r.get("market_ticker")
        ev = events.get(ticker, {})
        res_date = event_resolution_date(ev)

        urls = r.get("evidence_urls", []) or []
        n_urls = len(urls)

        n_marker = 0
        n_date_after = 0
        date_after_examples = []
        for u in urls:
            if is_marker_suspect(u):
                n_marker += 1
            pub = url_path_date(u)
            if pub is not None and res_date is not None and pub > res_date:
                n_date_after += 1
                if len(date_after_examples) < 3:
                    date_after_examples.append(
                        {"url": u[:160], "url_date": pub.isoformat()}
                    )

        # Parametric channel: clean iff event resolves AFTER model cutoff.
        if res_date is None:
            parametric_clean = None  # cannot determine
        else:
            parametric_clean = res_date > MODEL_CUTOFF
        if parametric_clean is False:
            n_parametric_suspect += 1

        total_urls += n_urls
        total_marker_suspect += n_marker
        total_datestamp_after += n_date_after
        if n_marker:
            n_events_with_marker += 1
        if n_date_after:
            n_events_with_datestamp += 1

        per_prediction.append(
            {
                "market_ticker": ticker,
                "resolved_date": res_date.isoformat() if res_date else None,
                "n_urls": n_urls,
                "n_marker_suspect": n_marker,
                "n_datestamp_after": n_date_after,
                "parametric_clean": parametric_clean,
                "datestamp_after_examples": date_after_examples,
            }
        )

    n_events = len(rows)
    summary = {
        "n_events": n_events,
        "total_urls": total_urls,
        # Retrieval channel, Method A (marker heuristic).
        "marker_suspect_urls": total_marker_suspect,
        "marker_suspect_pct": round(100 * total_marker_suspect / max(total_urls, 1), 1),
        "events_with_marker_suspect": n_events_with_marker,
        "events_with_marker_suspect_pct": round(
            100 * n_events_with_marker / max(n_events, 1), 1
        ),
        # Retrieval channel, Method B (datestamp-after-resolution, offline proxy).
        "datestamp_after_urls": total_datestamp_after,
        "datestamp_after_pct": round(100 * total_datestamp_after / max(total_urls, 1), 1),
        "events_with_datestamp_after": n_events_with_datestamp,
        # Parametric channel.
        "events_parametric_suspect": n_parametric_suspect,
        "events_parametric_suspect_pct": round(
            100 * n_parametric_suspect / max(n_events, 1), 1
        ),
    }
    return {"summary": summary, "per_prediction": per_prediction}


def main() -> int:
    events = {e["market_ticker"]: e for e in json.load(open(DATA / "resolved.json"))}

    results = {}
    for name, path in PRED_FILES.items():
        if not path.exists():
            print(f"WARNING: missing predictions file {path}; skipping")
            continue
        results[name] = label_file(load_rows(path), events)

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "leakage_labels.json"
    payload = {
        "model_cutoff": MODEL_CUTOFF.isoformat(),
        "method_notes": {
            "retrieval_A_marker": "URL-path marker heuristic (reused from check_retrieval_leakage.py).",
            "retrieval_B_datestamp": "URL-path publish-date AFTER resolution date; offline proxy for true page metadata (under-counts).",
            "parametric": "Event resolution date > model cutoff => clean (cannot be memorized).",
        },
        "files": results,
    }
    out.write_text(json.dumps(payload, indent=2))

    # --- Console summary table. ---
    cols = [n for n in PRED_FILES if n in results]
    print("=== Leakage labels: retrieval + parametric channels ===")
    print(f"model cutoff: {MODEL_CUTOFF.isoformat()}  (events resolving after = parametric-clean)")
    print()
    header = f"{'metric':<42}" + "".join(f"{c:>14}" for c in cols)
    print(header)
    print("-" * len(header))

    def row(label: str, key: str, suffix: str = ""):
        line = f"{label:<42}"
        for c in cols:
            v = results[c]["summary"][key]
            line += f"{str(v) + suffix:>14}"
        print(line)

    row("events", "n_events")
    row("evidence URLs", "total_urls")
    print("- retrieval channel A (markers) -")
    row("  marker-suspect URLs", "marker_suspect_urls")
    row("  marker-suspect URL rate", "marker_suspect_pct", "%")
    row("  events w/ any marker-suspect", "events_with_marker_suspect")
    row("  events w/ marker-suspect rate", "events_with_marker_suspect_pct", "%")
    print("- retrieval channel B (datestamp > resolution, offline proxy) -")
    row("  datestamp-after URLs", "datestamp_after_urls")
    row("  datestamp-after URL rate", "datestamp_after_pct", "%")
    row("  events w/ datestamp-after", "events_with_datestamp_after")
    print("- parametric channel (training-data memorization) -")
    row("  events parametric-suspect", "events_parametric_suspect")
    row("  events parametric-suspect rate", "events_parametric_suspect_pct", "%")
    print()

    if "brave" in results and "brave_fresh" in results:
        b = results["brave"]["summary"]["marker_suspect_pct"]
        bf = results["brave_fresh"]["summary"]["marker_suspect_pct"]
        print(
            f"retrieval (marker) leakage gap: brave {b}% vs brave_fresh {bf}% "
            f"(expected ~21% vs ~11%)"
        )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
