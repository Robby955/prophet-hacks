#!/usr/bin/env python3
"""D2: cross-model agreement heatmap (Plotly).

5 models × 26 events. Each cell = single-binary Brier for (model, event).
Colored on a diverging scale: dark green low loss → red high loss.
Hover shows model, event title, p_yes, actual, Brier. Click handler
opens the existing drill-down modal pattern (or just deep-links to
the side-by-side gallery anchored to that ticker).

Output: static/heatmap_resolved.html (standalone, Plotly via CDN).
"""
from __future__ import annotations

import html
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"
STATIC = REPO / "static"

MODELS = [
    ("Opus 4.7 (prod)", PRED / "multi_outcome_retrieval.json"),
    ("Opus 4.6", PRED / "ablation_claude-opus-4-6.json"),
    ("GPT-5.2", PRED / "ablation_gpt-5-2.json"),
    ("GPT-5.5", PRED / "ablation_gpt-5-5.json"),
    ("Gemini 3.1 Pro", PRED / "ablation_gemini-3-1-pro-preview.json"),
]


def load(path):
    raw = json.loads(path.read_text())
    rows = raw.get("predictions") if isinstance(raw, dict) else raw
    return {r["market_ticker"]: r for r in rows}


def main() -> int:
    events = {e["market_ticker"]: e for e in json.load(open(DATA / "resolved.json"))}
    actuals = json.load(open(DATA / "actuals.json"))
    preds_by_model = {name: load(path) for name, path in MODELS}

    # Sort events by category for cleaner row grouping.
    ev_sorted = sorted(events.values(), key=lambda e: (e.get("category", ""), e.get("title", "")))

    # Build z matrix: rows = events, cols = models. Brier per cell.
    z = []
    text_hover = []
    y_labels = []
    for ev in ev_sorted:
        t = ev["market_ticker"]
        a = actuals.get(t)
        if a is None: continue
        cat = ev.get("category", "?")
        short = (ev.get("title", "")[:50] + "…") if len(ev.get("title", "")) > 50 else ev.get("title", "")
        y_labels.append(f"{cat[:3].upper()} · {short}")
        row, hover_row = [], []
        for name, _ in MODELS:
            r = preds_by_model[name].get(t)
            if r is None:
                row.append(None); hover_row.append("no data")
            else:
                p = float(r["p_yes"])
                rationale = (r.get("rationale", "") or "").lower()
                parse_err = rationale.startswith("llm error") or "unparseable" in rationale
                b = (p - float(a)) ** 2
                row.append(b)
                err_tag = " (PARSE ERR)" if parse_err else ""
                hover_row.append(
                    f"<b>{html.escape(ev['title'][:80])}</b><br>"
                    f"{html.escape(cat)} · {len(ev.get('outcomes', []))} outcomes<br>"
                    f"model: {html.escape(name)}{err_tag}<br>"
                    f"p_yes = {p:.3f} · actual = {int(a)}<br>"
                    f"<b>Brier = {b:.4f}</b>"
                )
        z.append(row); text_hover.append(hover_row)

    body = f"""
<header>
  <h1>Cross-model agreement heatmap</h1>
  <p class="sub">26 resolved events × 5 LLM variants through the same pipeline. Each cell = single-binary Brier loss (PA CLI metric). <strong>Dark green</strong> = very low loss (model was confident in the right direction); <strong>red</strong> = high loss (confident-and-wrong). Rows sorted by category then title; hover for question and model rationale. Mean per column appears in the gallery footer.</p>
  <p class="links">
    <a href="/static/gallery_resolved.html">→ side-by-side gallery</a> ·
    <a href="/static/scatter_resolved.html">→ per-event scatter</a> ·
    <a href="/static/abstain_slider.html">→ abstain slider</a> ·
    <a href="/static/summary.html">→ summary report</a> ·
    <a href="/">→ home</a>
  </p>
</header>
<div id="plot" style="height: 720px;"></div>
<footer>
  <p>Built by <code>scripts/build_d2_heatmap.py</code>. Source: <code>data/predictions/multi_outcome_retrieval.json</code> + <code>data/predictions/ablation_*.json</code> + <code>data/actuals.json</code>. Brier scale: green &lt; 0.05, yellow ≈ 0.15, red &gt; 0.30. Where the entire row is green, models agree on a correct confident pick; mixed rows are where category specialization or schema discipline matters.</p>
</footer>
<script id="hm-z" type="application/json">{json.dumps(z)}</script>
<script id="hm-text" type="application/json">{json.dumps(text_hover)}</script>
<script id="hm-ylabels" type="application/json">{json.dumps(y_labels)}</script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script>
const z = JSON.parse(document.getElementById('hm-z').textContent);
const text = JSON.parse(document.getElementById('hm-text').textContent);
const y = JSON.parse(document.getElementById('hm-ylabels').textContent);
const x = {json.dumps([m[0] for m in MODELS])};

Plotly.newPlot('plot', [{{
  z: z, x: x, y: y,
  type: 'heatmap',
  colorscale: [
    [0.0, '#1e6f3a'],
    [0.05, '#bbe5b0'],
    [0.15, '#f6f4c5'],
    [0.30, '#fad9b3'],
    [0.55, '#f5b7b1'],
    [1.0, '#7a1a1a'],
  ],
  zmin: 0, zmax: 1,
  text: text,
  hovertemplate: '%{{text}}<extra></extra>',
  showscale: true,
  colorbar: {{ title: 'Brier loss', tickformat: '.2f', len: 0.7 }},
}}], {{
  margin: {{ l: 380, r: 60, t: 30, b: 60 }},
  xaxis: {{ side: 'top', tickfont: {{ size: 12, color: '#1a1f2c' }} }},
  yaxis: {{ tickfont: {{ size: 10 }}, autorange: 'reversed' }},
  plot_bgcolor: '#fafbfc',
  paper_bgcolor: '#fafbfc',
  hoverlabel: {{ bgcolor: 'white', bordercolor: '#1a1f2c', font: {{ size: 12 }}, align: 'left' }},
}}, {{ responsive: true, displaylogo: false }});
</script>
"""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cross-model heatmap · ForecastingPath</title>
<style>
body {{ font: 14px/1.45 -apple-system, system-ui, sans-serif; color: #1a1f2c; background: #fafbfc; margin: 0; padding: 20px 28px; }}
header {{ max-width: 1300px; margin: 0 auto 14px; }}
header h1 {{ font-size: 22px; margin: 0 0 6px; }}
header .sub {{ color: #475066; margin: 4px 0; font-size: 13px; max-width: 1100px; }}
header .links {{ font-size: 13px; margin-top: 6px; }}
header .links a {{ color: #2856a3; text-decoration: none; margin-right: 12px; }}
#plot {{ max-width: 1300px; margin: 0 auto; background: white; border: 1px solid #e6e9f0; border-radius: 8px; }}
footer {{ max-width: 1300px; margin: 12px auto; color: #6a7388; font-size: 12px; }}
footer code {{ background: #eef0f5; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    (STATIC / "heatmap_resolved.html").write_text(page)
    print(f"wrote static/heatmap_resolved.html ({len(z)} events x {len(MODELS)} models)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
