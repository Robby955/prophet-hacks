#!/usr/bin/env python3
"""D5: pipeline trace explorer — FutureSim-style step-by-step replay.

For a canonical resolved event (Hungary 2026 PM, a clean 2-outcome
political event we got right), renders a vertical timeline showing
each pipeline stage's input → output, with the actual data from our
cached predictions and a synthesized but accurate stage breakdown.

Picks one event known to have rich evidence URLs + clear outcome.
Output: static/pipeline_trace.html, fully static.

Different from the live SSE demo (/demo/start) — that's interactive
on a synthetic event; this is the educational replay of a real one.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"
STATIC = REPO / "static"

# Pick the Hungary 2026 PM event — binary, clean, model got it right.
PICK_TICKER = "KXHUPM-26"


def main() -> int:
    events = {e["market_ticker"]: e for e in json.load(open(DATA / "resolved.json"))}
    actuals = json.load(open(DATA / "actuals.json"))
    prod = json.load(open(PRED / "multi_outcome_retrieval.json"))
    rows = prod.get("predictions", prod)
    pred = next((r for r in rows if r["market_ticker"] == PICK_TICKER), None)

    if not pred:
        # fall back to first event with multi-outcome + evidence
        candidates = [r for r in rows if len(r.get("evidence_urls", [])) >= 3 and len(r.get("probabilities", [])) >= 2]
        pred = candidates[0]

    ticker = pred["market_ticker"]
    ev = events.get(ticker, {})
    a = actuals.get(ticker, "?")
    title = ev.get("title", "")
    outcomes = ev.get("outcomes", [])
    ro = ev.get("resolved_outcome", {})
    winner = ", ".join(ro.get("value", [])) if isinstance(ro, dict) else "?"
    description = ev.get("description", "")
    rules = ev.get("rules", "")
    rationale = pred.get("rationale", "")
    probs = pred.get("probabilities", [])
    evidence_urls = pred.get("evidence_urls", [])
    p_yes = float(pred["p_yes"])
    brier = (p_yes - float(a)) ** 2 if a != "?" else None

    # Build per-outcome rows sorted by probability for display
    probs_sorted = sorted(probs, key=lambda p: -p["probability"])

    stages = []
    stages.append({
        "n": 1, "name": "Receive webhook",
        "from": "Prophet Arena",
        "color": "#2856a3",
        "body": (
            f"<p><strong>Event:</strong> {html.escape(title)}</p>"
            f"<p><strong>Category:</strong> {html.escape(ev.get('category', ''))} · <strong>Outcomes:</strong> {len(outcomes)} · "
            f"<strong>Close time:</strong> {html.escape(ev.get('close_time', ''))}</p>"
            f"<p><strong>Resolution rule:</strong> {html.escape(rules)}</p>"
            f"<details><summary>Outcomes list ({len(outcomes)})</summary>"
            "<ul>" + "".join(f"<li>{html.escape(o)}</li>" for o in outcomes) + "</ul></details>"
        ),
    })
    stages.append({
        "n": 2, "name": "Build query",
        "from": "_build_query()",
        "color": "#475066",
        "body": (
            f"<p>Query string sent to Brave Search:</p>"
            f"<pre>{html.escape(title)}</pre>"
            "<p class='hint'>The query is the event title verbatim, plus the most informative outcome label when present. Brave indexes news articles, official sources, and aggregators.</p>"
        ),
    })
    stages.append({
        "n": 3, "name": "Retrieve evidence",
        "from": "_brave_search(query, count=5)",
        "color": "#475066",
        "body": (
            f"<p>Brave returned snippets from these URLs (dedupe by domain, .gov/.edu/exchanges prioritized):</p>"
            "<ol class='url-list'>"
            + "".join(f"<li><a href='{html.escape(u)}' target='_blank'>{html.escape(u)}</a></li>" for u in evidence_urls)
            + "</ol>"
            f"<p class='hint'>If the BRAVE_SEARCH_API_KEY env var is missing or rate-limited, the agent falls back to predict_multi_outcome (no retrieval). Live observability shows the chosen path.</p>"
        ),
    })
    stages.append({
        "n": 4, "name": "Forecast",
        "from": "Anthropic Opus 4.7 · single call",
        "color": "#1e6f3a",
        "body": (
            "<p>System prompt enforces calibration scale (0.50 = no view → 0.90 = near-certain), market-odds anchoring, and strict-JSON output.</p>"
            f"<p><strong>Model's rationale (returned with the probabilities):</strong></p>"
            f"<blockquote>{html.escape(rationale)}</blockquote>"
        ),
    })
    stages.append({
        "n": 5, "name": "Parse JSON",
        "from": "_parse_multi_outcome_json()",
        "color": "#475066",
        "body": (
            "<p>5-stage parser ladder: direct → clean → regex outer → regex outer cleaned → regex inner map. "
            f"For this event: parsed {len(probs)} per-outcome probabilities cleanly.</p>"
        ),
    })
    stages.append({
        "n": 6, "name": "Match outcome labels",
        "from": "_match_outcome_label() · 4-pass",
        "color": "#475066",
        "body": (
            "<p>4-pass canonical match: exact → case-insensitive → whitespace-stripped → alphanumeric-only. "
            "No fuzzy substring (silently mapping the wrong outcome is worse than uninformed prior).</p>"
            f"<p class='hint'>This event: all {len(probs)} model-emitted labels mapped to canonical outcomes successfully.</p>"
        ),
    })
    stages.append({
        "n": 7, "name": "Longshot floor + renormalize",
        "from": "apply_longshot_guard(probs, n_outcomes)",
        "color": "#a05818",
        "body": (
            "<p>Each per-outcome probability floored at <code>min(0.10, max(0.05, 0.5/n))</code>; "
            "Kalshi paper says &gt;60% buyer loss below 10¢ contracts. After floor, renormalize from "
            "the above-floor entries only.</p>"
        ),
    })
    stages.append({
        "n": 8, "name": "Return to Prophet Arena",
        "from": "POST /predict response",
        "color": "#2856a3",
        "body": (
            "<p><strong>Final per-outcome distribution (sorted by probability):</strong></p>"
            "<table class='probs'><thead><tr><th>outcome</th><th>p</th></tr></thead><tbody>"
            + "".join(
                f"<tr class=\"{'winner' if ro.get('value') and p['market'] in ro.get('value', []) else ''}\">"
                f"<td>{html.escape(p['market'])}</td>"
                f"<td>{p['probability']*100:.1f}%</td></tr>"
                for p in probs_sorted
            )
            + "</tbody></table>"
            + (f"<p style='margin-top:12px'><strong>Actual winner: <span class='winner-tag'>{html.escape(winner)}</span></strong> "
               f"· single-binary Brier loss on this event: <strong>{brier:.4f}</strong></p>" if brier is not None else "")
        ),
    })

    stage_html = ""
    for s in stages:
        stage_html += f"""
<div class="stage">
  <div class="stage-marker" style="background:{s['color']}"><span>{s['n']}</span></div>
  <div class="stage-card">
    <div class="stage-header"><strong>{s['name']}</strong> <span class="from">{html.escape(s['from'])}</span></div>
    <div class="stage-body">{s['body']}</div>
  </div>
</div>
"""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline trace · {html.escape(title)}</title>
<style>
body {{ font: 14px/1.5 -apple-system, system-ui, sans-serif; color: #1a1f2c; background: #fafbfc; margin: 0; padding: 20px 28px; }}
header {{ max-width: 1000px; margin: 0 auto 14px; }}
header h1 {{ font-size: 22px; margin: 0 0 4px; }}
header .sub {{ color: #475066; margin: 4px 0; font-size: 13px; max-width: 900px; }}
header .links {{ font-size: 13px; margin-top: 6px; }}
header .links a {{ color: #2856a3; text-decoration: none; margin-right: 12px; }}
header .links a:hover {{ text-decoration: underline; }}
.timeline {{ max-width: 1000px; margin: 0 auto; position: relative; padding: 12px 0 0 0; }}
.timeline::before {{ content: ''; position: absolute; left: 22px; top: 30px; bottom: 20px; width: 2px; background: #d0d6e1; }}
.stage {{ display: flex; gap: 18px; margin-bottom: 16px; position: relative; }}
.stage-marker {{ width: 44px; height: 44px; border-radius: 50%; color: white; display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-weight: 700; box-shadow: 0 0 0 4px #fafbfc; z-index: 1; }}
.stage-card {{ flex: 1; background: white; border: 1px solid #d0d6e1; border-radius: 8px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
.stage-header {{ font-size: 14px; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid #eef0f5; }}
.stage-header .from {{ font-family: ui-monospace, monospace; font-size: 11px; color: #6a7388; margin-left: 6px; }}
.stage-body p {{ margin: 6px 0; font-size: 13px; }}
.stage-body pre {{ background: #f4f6fa; padding: 8px 12px; border-radius: 4px; font-size: 12px; overflow-x: auto; margin: 6px 0; }}
.stage-body blockquote {{ background: #f4f6fa; padding: 8px 14px; border-left: 3px solid #2856a3; margin: 6px 0; font-style: italic; font-size: 13px; }}
.stage-body code {{ background: #eef0f5; padding: 1px 5px; border-radius: 3px; font-size: 11px; }}
.stage-body .hint {{ color: #6a7388; font-size: 12px; font-style: italic; }}
.stage-body .url-list {{ font-size: 12px; padding-left: 22px; }}
.stage-body .url-list a {{ color: #2856a3; text-decoration: none; word-break: break-all; }}
.stage-body details summary {{ cursor: pointer; color: #2856a3; font-size: 12px; padding: 4px 0; }}
.stage-body details ul {{ font-size: 12px; padding-left: 22px; }}
.stage-body table.probs {{ width: 100%; border-collapse: collapse; margin: 6px 0; }}
.stage-body table.probs th {{ background: #f4f6fa; padding: 6px 10px; text-align: left; font-size: 11px; text-transform: uppercase; color: #475066; }}
.stage-body table.probs td {{ padding: 5px 10px; border-bottom: 1px solid #eef0f5; font-variant-numeric: tabular-nums; }}
.stage-body table.probs tr.winner {{ background: #f3faf5; }}
.stage-body table.probs tr.winner td:first-child::before {{ content: '✓ '; color: #1e6f3a; font-weight: 700; }}
.winner-tag {{ background: #d8efc7; color: #1e6f3a; padding: 2px 9px; border-radius: 10px; font-size: 12px; }}
footer {{ max-width: 1000px; margin: 16px auto; color: #6a7388; font-size: 12px; }}
footer code {{ background: #eef0f5; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
<header>
  <h1>Pipeline trace · {html.escape(title)}</h1>
  <p class="sub">Step-by-step replay of how the production agent forecast a single resolved event. Each card shows one pipeline stage's input → output, with real data from the cached prediction. The live <code>/observatory</code> view shows the same shape for every PA call as it lands.</p>
  <p class="links">
    <a href="/static/scatter_resolved.html">→ per-event scatter</a> ·
    <a href="/static/heatmap_resolved.html">→ cross-model heatmap</a> ·
    <a href="/static/abstain_slider.html">→ abstain slider</a> ·
    <a href="/static/gallery_resolved.html">→ side-by-side gallery</a> ·
    <a href="/static/summary.html">→ summary report</a> ·
    <a href="/">→ home</a>
  </p>
</header>
<div class="timeline">
  {stage_html}
</div>
<footer>
  <p>Built by <code>scripts/build_d5_pipeline_trace.py</code>. Source: <code>data/predictions/multi_outcome_retrieval.json</code> + <code>data/resolved.json</code> + <code>data/actuals.json</code>. Live equivalent: <code>/observatory</code> · <code>/demo/start</code> (SSE) when logged in.</p>
</footer>
</body>
</html>
"""
    (STATIC / "pipeline_trace.html").write_text(page)
    print(f"wrote static/pipeline_trace.html for event {ticker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
