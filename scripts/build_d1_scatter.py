#!/usr/bin/env python3
"""D1: per-event Plotly scatter — production p_yes vs Brier loss, colored by outcome.

Renders an interactive standalone HTML with Plotly via CDN; no Python
Plotly dependency required at build time. Output goes to
static/scatter_resolved.html.

Each point:
- x = production p_yes (model's probability for outcomes[0])
- y = single-binary Brier loss on this event
- color = green (actual=1, model bet on it correctly) / red (actual=0)
- size = n_outcomes (multi-outcome events are bigger)
- hover = title, category, winner, rationale, evidence URLs
"""
from __future__ import annotations

import html
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"
STATIC = REPO / "static"


def main() -> int:
    events = {e["market_ticker"]: e for e in json.load(open(DATA / "resolved.json"))}
    actuals = json.load(open(DATA / "actuals.json"))
    prod = json.load(open(PRED / "multi_outcome_retrieval.json"))
    rows = prod.get("predictions", prod)

    points = []
    for r in rows:
        t = r["market_ticker"]
        ev = events.get(t, {})
        a = actuals.get(t)
        if a is None:
            continue
        p = float(r["p_yes"])
        brier = (p - float(a)) ** 2
        outcomes = ev.get("outcomes", [])
        ro = ev.get("resolved_outcome", {})
        ro_val = ro.get("value", []) if isinstance(ro, dict) else []
        winner = ", ".join(str(x) for x in ro_val) if ro_val else "?"
        rationale = (r.get("rationale", "") or "")[:240]
        points.append({
            "ticker": t,
            "title": ev.get("title", "")[:120],
            "category": ev.get("category", ""),
            "p_yes": round(p, 4),
            "brier": round(brier, 5),
            "actual": int(a),
            "n_outcomes": len(outcomes),
            "winner": winner,
            "outcome0": outcomes[0] if outcomes else "?",
            "rationale": rationale,
        })

    points.sort(key=lambda d: d["p_yes"])
    data_json = json.dumps(points, ensure_ascii=False)

    body = f"""
<header>
  <h1>Per-event scatter · production (Opus 4.7)</h1>
  <div class="read-panel"><strong>What you're looking at:</strong> one dot per resolved event; good predictions sit near the bottom edge, and clicking or hovering a dot reveals the event, winner, rationale, and ticker.</div>
  <p class="sub">Each point = one of the 26 resolved events. <strong>X</strong> = production p_yes for <em>outcomes[0]</em>. <strong>Y</strong> = single-binary Brier loss. <strong>Green</strong> = outcomes[0] won; <strong>red</strong> = outcomes[0] lost. <strong>Size</strong> = n_outcomes (bigger circles = harder multi-outcome events). Hover for question + winner + rationale.</p>
  <p class="sub">Read the picture: green dots on the right (high p_yes for the actual winner) → low Brier (good); red dots on the right (high p_yes when outcomes[0] LOST) → high Brier (bad confident-and-wrong). Green dots on the left (low p_yes when outcomes[0] WON) → also high Brier. The shape of the curve shows where our model's confidence helped or hurt.</p>
  <p class="links">
    <a href="/static/gallery_resolved.html">→ side-by-side gallery</a> ·
    <a href="/static/summary.html">→ summary report</a> ·
    <a href="/">→ home</a>
  </p>
</header>
<div id="plot" style="height: 620px;"></div>
<footer>
  <p>Built by <code>scripts/build_d1_scatter.py</code>. Source: <code>data/predictions/multi_outcome_retrieval.json</code> + <code>data/resolved.json</code> + <code>data/actuals.json</code>. Brier = single-binary (PA CLI metric).</p>
</footer>
<script id="scatter-data" type="application/json">{data_json}</script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script>
const pts = JSON.parse(document.getElementById('scatter-data').textContent);
const green = pts.filter(p => p.actual === 1);
const red = pts.filter(p => p.actual === 0);
function trace(pts, color, name) {{
  return {{
    x: pts.map(p => p.p_yes),
    y: pts.map(p => p.brier),
    mode: 'markers+text',
    type: 'scatter',
    name: name,
    text: pts.map(p => p.n_outcomes >= 8 ? String(p.n_outcomes) : ''),
    textposition: pts.map((p, i) => {{
      if (p.n_outcomes < 8) return 'middle right';
      const positions = ['top left', 'top right', 'bottom left', 'bottom right', 'middle left', 'middle right'];
      return positions[i % positions.length];
    }}),
    textfont: {{ size: 11, color: 'rgba(71,80,102,0.72)' }},
    marker: {{
      size: pts.map(p => Math.min(45, 12 + p.n_outcomes * 1.2)),
      color: color,
      opacity: 0.68,
      line: {{ color: '#1a1f2c', width: 1 }},
    }},
    cliponaxis: false,
    customdata: pts.map(p => [p.title, p.category, p.winner, p.outcome0, p.rationale, p.n_outcomes, p.ticker]),
    hovertemplate: '<b>%{{customdata[0]}}</b><br>' +
                   '<span style="color:#475066">%{{customdata[1]}} · %{{customdata[5]}} outcomes</span><br><br>' +
                   'production: p(<i>%{{customdata[3]}}</i>) = %{{x:.3f}}<br>' +
                   'actual winner: <b>%{{customdata[2]}}</b><br>' +
                   'Brier loss: %{{y:.4f}}<br><br>' +
                   '<i>%{{customdata[4]}}</i><br>' +
                   '<span style="color:#b0b7c4">%{{customdata[6]}}</span>' +
                   '<extra></extra>',
  }};
}}
const traces = [
  trace(green, '#1e6f3a', 'outcomes[0] won (we want high p)'),
  trace(red,   '#a02828', 'outcomes[0] lost (we want low p)'),
];
const layout = {{
  margin: {{ l: 60, r: 30, t: 20, b: 60 }},
  xaxis: {{
    title: 'production p_yes (probability assigned to outcomes[0])',
    range: [-0.04, 1.04],
    gridcolor: '#eef0f5',
    zerolinecolor: '#d0d6e1',
    tickformat: '.1f',
  }},
  yaxis: {{
    title: 'single-binary Brier loss',
    range: [-0.02, Math.max(0.4, Math.max(...pts.map(p => p.brier)) + 0.04)],
    gridcolor: '#eef0f5',
  }},
  legend: {{ x: 0.55, y: 0.98, bgcolor: 'rgba(255,255,255,0.85)', bordercolor: '#d0d6e1', borderwidth: 1 }},
  plot_bgcolor: 'white',
  paper_bgcolor: '#fafbfc',
  hoverlabel: {{ bgcolor: 'white', bordercolor: '#1a1f2c', font: {{ size: 12 }}, align: 'left' }},
  shapes: [
    {{ type: 'line', x0: 0, x1: 1, y0: 0, y1: 1, line: {{ color: '#fad9b3', width: 1, dash: 'dash' }} }},
    {{ type: 'line', x0: 1, x1: 0, y0: 0, y1: 1, line: {{ color: '#fad9b3', width: 1, dash: 'dash' }} }},
  ],
  annotations: [
    {{ x: 0.5, y: 0.05, text: 'low p_yes + outcomes[0] lost: good (low Brier)', showarrow: false, font: {{ size: 11, color: '#1e6f3a' }} }},
  ],
}};
Plotly.newPlot('plot', traces, layout, {{ responsive: true, displaylogo: false }});
</script>
"""

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Per-event scatter · ForecastingPath</title>
<style>
body {{ font: 14px/1.45 -apple-system, system-ui, sans-serif; color: #1a1f2c; background: #fafbfc; margin: 0; padding: 20px 28px; }}
header {{ max-width: 1200px; margin: 0 auto 14px; }}
header h1 {{ font-size: 22px; margin: 0 0 6px; }}
.read-panel {{ margin: 8px 0 10px; padding: 10px 12px; max-width: 1000px; border: 1px solid #d8dde8; border-left: 4px solid #2856a3; border-radius: 6px; background: #f4f7fb; color: #1a1f2c; font-size: 13px; }}
header .sub {{ color: #475066; margin: 4px 0; font-size: 13px; max-width: 1000px; }}
header .links {{ font-size: 13px; margin-top: 6px; }}
header .links a {{ color: #2856a3; text-decoration: none; margin-right: 12px; }}
header .links a:hover {{ text-decoration: underline; }}
#plot {{ max-width: 1200px; margin: 0 auto; background: white; border: 1px solid #e6e9f0; border-radius: 8px; }}
footer {{ max-width: 1200px; margin: 12px auto; color: #6a7388; font-size: 12px; }}
footer code {{ background: #eef0f5; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    (STATIC / "scatter_resolved.html").write_text(html_out)
    print(f"wrote static/scatter_resolved.html ({len(points)} points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
