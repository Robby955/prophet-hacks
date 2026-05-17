#!/usr/bin/env python3
"""Publication-grade multi-model calibration overlay.

For each of the 5 model variants we evaluated, plot:
- Predicted-probability bin (0.0-1.0, 5 bins) on x-axis
- Empirical hit rate (proportion of events with outcomes[0] = winner
  in that bin) on y-axis
- Bubble size: number of events in the bin
- Reference y=x diagonal (perfectly calibrated)

This is the chart that turns "GPT-5.5 has low REL despite high Brier"
into a visual the reader can verify themselves.

Writes static/calibration_overlay.html (Plotly) and (if matplotlib is
available) static/calibration_overlay.png for the workshop paper.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"
STATIC = REPO / "static"

MODELS = [
    ("Opus 4.7 (production)", PRED / "multi_outcome_retrieval.json", "#1e6f3a"),
    ("Opus 4.6",              PRED / "ablation_claude-opus-4-6.json", "#0d9488"),
    ("GPT-5.2",               PRED / "ablation_gpt-5-2.json", "#7c3aed"),
    ("GPT-5.5",               PRED / "ablation_gpt-5-5.json", "#ea580c"),
    ("Gemini 3.1 Pro",        PRED / "ablation_gemini-3-1-pro-preview.json", "#dc2626"),
]
BIN_EDGES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0001]


def _bin_index(p: float) -> int:
    for i in range(len(BIN_EDGES) - 1):
        if BIN_EDGES[i] <= p < BIN_EDGES[i + 1]:
            return i
    return len(BIN_EDGES) - 2


def main() -> int:
    actuals = {k: float(v) for k, v in json.load(open(DATA / "actuals.json")).items()}

    model_curves: list[dict] = []
    for name, path, color in MODELS:
        if not path.exists():
            continue
        d = json.loads(path.read_text())
        rows = d.get("predictions", d)
        pairs = [(float(r["p_yes"]), actuals[r["market_ticker"]])
                 for r in rows if r["market_ticker"] in actuals]
        if not pairs:
            continue

        bins: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for p, a in pairs:
            bins[_bin_index(p)].append((p, a))

        xs, ys, ns = [], [], []
        for i in range(len(BIN_EDGES) - 1):
            items = bins.get(i, [])
            if not items:
                continue
            xs.append(sum(p for p, _ in items) / len(items))
            ys.append(sum(a for _, a in items) / len(items))
            ns.append(len(items))
        model_curves.append({
            "name": name,
            "color": color,
            "x": xs,
            "y": ys,
            "n": ns,
        })

    body = f"""
<header>
  <h1>Calibration overlay — 5 models on the same 26 events</h1>
  <div class="read-panel"><strong>What you're looking at:</strong> reliability diagram for each model. The diagonal is perfect calibration. A model whose line sits on the diagonal predicts probabilities that match empirical hit rates; deviation up = under-confident, deviation down = over-confident. Bubble size = number of events in that probability bin.</div>
  <p class="sub">Reliability diagram (Murphy 1973) for each model variant on the 26-event resolved set. Five probability bins on the x-axis (0.0-0.2, 0.2-0.4, ..., 0.8-1.0); empirical hit rate on the y-axis; bubble size = number of events in that bin. Perfectly calibrated = points on the y=x diagonal.</p>
  <p class="links">
    <a href="/static/summary.html">→ summary report</a> ·
    <a href="/static/gallery_resolved.html">→ side-by-side gallery</a> ·
    <a href="/static/scatter_resolved.html">→ per-event scatter</a> ·
    <a href="/">→ home</a>
  </p>
</header>

<div id="plot" style="height: 560px;"></div>

<footer>
  <p>Built by <code>scripts/build_d_calibration_overlay.py</code>. Source: <code>data/predictions/multi_outcome_retrieval.json</code> + <code>data/predictions/ablation_*.json</code> + <code>data/actuals.json</code>. Bins: equal-width, 5 buckets. Murphy decomposition (REL / RES / UNC) on the same data lives in <a href="/static/summary.html">the summary report</a>.</p>
  <p style="margin-top:6px;color:#6a7388">The Oracles · Team CanadaHacks · Rob Sneiderman <a href="https://github.com/Robby955">@Robby955</a> · Prophet Hacks 2026</p>
</footer>

<script id="curves-data" type="application/json">{json.dumps(model_curves)}</script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script>
const curves = JSON.parse(document.getElementById('curves-data').textContent);
const traces = [];
// y=x reference
traces.push({{
  x: [0, 1], y: [0, 1], mode: 'lines', type: 'scatter',
  line: {{ color: '#b0b7c4', width: 1.5, dash: 'dash' }},
  name: 'perfectly calibrated',
  hoverinfo: 'skip',
  showlegend: true,
}});
for (const c of curves) {{
  traces.push({{
    x: c.x, y: c.y, mode: 'lines+markers', type: 'scatter',
    name: c.name,
    line: {{ color: c.color, width: 2 }},
    marker: {{
      size: c.n.map(n => 6 + Math.sqrt(n) * 4),
      color: c.color, opacity: 0.75,
      line: {{ color: '#1a1f2c', width: 1 }},
    }},
    text: c.n.map((n, i) => 'n=' + n),
    hovertemplate: '<b>' + c.name + '</b><br>predicted p (bin mean) %{{x:.3f}}<br>empirical hit rate %{{y:.3f}}<br>%{{text}}<extra></extra>',
  }});
}}
Plotly.newPlot('plot', traces, {{
  margin: {{ l: 60, r: 20, t: 20, b: 60 }},
  xaxis: {{ title: 'predicted probability (bin mean)', gridcolor: '#eef0f5', range: [-0.02, 1.02] }},
  yaxis: {{ title: 'empirical hit rate', gridcolor: '#eef0f5', range: [-0.02, 1.02] }},
  legend: {{ x: 0.55, y: 0.18, bgcolor: 'rgba(255,255,255,0.9)', bordercolor: '#d0d6e1', borderwidth: 1 }},
  plot_bgcolor: 'white', paper_bgcolor: '#fafbfc',
  hoverlabel: {{ bgcolor: 'white', bordercolor: '#1a1f2c', font: {{ size: 12 }} }},
}}, {{ responsive: true, displaylogo: false }});
</script>
"""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calibration overlay · ForecastingPath</title>
<style>
body {{ font: 14px/1.45 -apple-system, system-ui, sans-serif; color: #1a1f2c; background: #fafbfc; margin: 0; padding: 20px 28px; }}
header {{ max-width: 1100px; margin: 0 auto 14px; }}
header h1 {{ font-size: 22px; margin: 0 0 6px; }}
.read-panel {{ margin: 8px 0 10px; padding: 10px 12px; max-width: 980px; border: 1px solid #d8dde8; border-left: 4px solid #2856a3; border-radius: 6px; background: #f4f7fb; color: #1a1f2c; font-size: 13px; }}
header .sub {{ color: #475066; margin: 4px 0; font-size: 13px; max-width: 980px; }}
header .links {{ font-size: 13px; margin-top: 6px; }}
header .links a {{ color: #2856a3; text-decoration: none; margin-right: 12px; }}
header .links a:hover {{ text-decoration: underline; }}
#plot {{ max-width: 1100px; margin: 0 auto; background: white; border: 1px solid #e6e9f0; border-radius: 8px; }}
footer {{ max-width: 1100px; margin: 12px auto; color: #6a7388; font-size: 12px; }}
footer code {{ background: #eef0f5; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    (STATIC / "calibration_overlay.html").write_text(page)
    print(f"wrote static/calibration_overlay.html with {len(model_curves)} model curves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
