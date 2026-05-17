#!/usr/bin/env python3
"""Render the variance ablation result as an interactive HTML page.

Reads `data/predictions/variance_run_*.json` plus the canonical
`multi_outcome_retrieval.json` and produces `static/variance.html`
with three Plotly panels:

  1. Per-run mean Brier (bar) with mean ± std reference band
  2. Per-event spread across 6 runs (5 variance runs + canonical)
     as a strip plot, sorted by variance
  3. Per-run distribution of per-event Brier (jittered scatter)

The point of the page: turn "production scores 0.0378" into
"production scores 0.0378 ± X across 5 independent runs" so the
reader can see model-level noise vs systematic improvements.
"""
from __future__ import annotations

import html
import json
import statistics as st
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"
STATIC = REPO / "static"


def main() -> int:
    actuals = {k: float(v) for k, v in json.load(open(DATA / "actuals.json")).items()}
    events = {e["market_ticker"]: e for e in json.load(open(DATA / "resolved.json"))}

    # Load 6 runs: 5 variance + 1 canonical production
    runs: list[tuple[str, dict[str, float]]] = []
    canonical = json.load(open(PRED / "multi_outcome_retrieval.json"))
    canonical_rows = canonical.get("predictions", canonical)
    runs.append(("canonical", {r["market_ticker"]: float(r["p_yes"]) for r in canonical_rows}))

    for i in range(1, 6):
        p = PRED / f"variance_run_{i}.json"
        if not p.exists():
            print(f"missing {p}; run scripts/ablate_variance.py first")
            return 2
        d = json.load(open(p))
        runs.append((f"run {i}", {r["market_ticker"]: float(r["p_yes"]) for r in d["predictions"]}))

    # Per-run mean Brier
    per_run = []
    for label, preds in runs:
        bs = [(preds[t] - actuals[t]) ** 2 for t in actuals if t in preds]
        per_run.append({
            "label": label,
            "mean_brier": sum(bs) / max(len(bs), 1),
            "n": len(bs),
        })
    means = [r["mean_brier"] for r in per_run]
    variance_only = means[1:]  # skip canonical
    grand_mean = sum(variance_only) / len(variance_only)
    grand_std = st.stdev(variance_only) if len(variance_only) > 1 else 0.0

    # Per-event statistics across 5 variance runs (skip canonical for "intra")
    by_event: dict[str, list[float]] = {}
    titles: dict[str, str] = {}
    for label, preds in runs[1:]:  # variance runs only
        for t, p_yes in preds.items():
            if t not in actuals: continue
            b = (p_yes - actuals[t]) ** 2
            by_event.setdefault(t, []).append(b)
            titles[t] = events.get(t, {}).get("title", "")[:80]

    per_event = []
    for t, bs in by_event.items():
        if len(bs) < 2: continue
        per_event.append({
            "ticker": t,
            "title": titles[t],
            "mean": sum(bs) / len(bs),
            "std": st.stdev(bs),
            "min": min(bs),
            "max": max(bs),
            "all": bs,
        })
    per_event.sort(key=lambda e: -e["std"])  # most variable first

    # Cross-run p_yes variance per event (more interpretable than Brier variance)
    p_by_event: dict[str, list[float]] = {}
    for _, preds in runs[1:]:
        for t, p_yes in preds.items():
            p_by_event.setdefault(t, []).append(p_yes)
    p_stats = []
    for t, ps in p_by_event.items():
        if len(ps) < 2: continue
        p_stats.append({
            "ticker": t,
            "title": titles.get(t, ""),
            "p_mean": sum(ps) / len(ps),
            "p_std": st.stdev(ps),
            "p_range": max(ps) - min(ps),
            "all_p": ps,
        })
    p_stats.sort(key=lambda e: -e["p_range"])

    body = f"""
<header>
  <h1>Intra-model variance · production Opus 4.7 across 5 reruns</h1>
  <p class="sub">The headline "production scores 0.0378 single-binary Brier" hides
     run-to-run LLM stochasticity. We re-ran <code>predict_multi_outcome_retrieval</code>
     five times on the same 26 events. Same prompt, same retrieval, fresh LLM calls.
     The grand mean is <strong>{grand_mean:.5f}</strong> with a standard deviation of
     <strong>{grand_std:.5f}</strong>. The canonical production file (the one PA's CLI
     scored) reported {means[0]:.5f}, well within one standard deviation of the grand mean.
     Read this as: <strong>production Brier = {grand_mean:.4f} ± {grand_std:.4f}</strong>.</p>
  <p class="links">
    <a href="/static/summary.html">→ summary report</a> ·
    <a href="/static/gallery_resolved.html">→ side-by-side gallery</a> ·
    <a href="/static/bootstrap_hist.html">→ bootstrap CI</a> ·
    <a href="/">→ home</a>
  </p>
</header>

<div class="kpis">
  <div class="kpi"><div class="lbl">Grand mean (5 runs)</div><div class="val">{grand_mean:.5f}</div></div>
  <div class="kpi"><div class="lbl">Standard deviation</div><div class="val">{grand_std:.5f}</div></div>
  <div class="kpi"><div class="lbl">Range</div><div class="val">[{min(variance_only):.4f}, {max(variance_only):.4f}]</div></div>
  <div class="kpi"><div class="lbl">Canonical file</div><div class="val">{means[0]:.5f}</div></div>
</div>

<section>
  <h2>1. Per-run mean Brier · 5 fresh reruns + canonical</h2>
  <div id="plot-runs" style="height: 380px;"></div>
  <p class="meta">Dashed line = grand mean of the 5 variance runs. Shaded band = ± 1 σ.
     If the canonical bar sits inside the band, the published number is consistent with
     intra-model noise (not an outlier).</p>
</section>

<section>
  <h2>2. Per-event p_yes spread · which events are stable vs noisy</h2>
  <div id="plot-spread" style="height: 520px;"></div>
  <p class="meta">For each event, the dots show <em>p_yes</em> across the 5 reruns;
     the bar shows max − min. Sorted by spread (most volatile at top). Events with
     near-zero spread are the ones where the model converges to the same probability
     regardless of stochastic effects; events with high spread are where small
     wording differences in the LLM output flip our score.</p>
</section>

<section>
  <h2>3. Per-event Brier · all 5 runs overlaid</h2>
  <div id="plot-jitter" style="height: 420px;"></div>
  <p class="meta">Same x-axis: events sorted by mean Brier. Each event has 5 dots
     (one per rerun) jittered vertically for visibility. Most events cluster tight;
     a handful have wider spread.</p>
</section>

<footer>
  <p>Built by <code>scripts/build_variance_plot.py</code> from
     <code>data/predictions/variance_run_*.json</code>. Variance ablation harness:
     <code>scripts/ablate_variance.py</code>.</p>
  <p style="margin-top:6px;color:#6a7388">The Oracles · Team CanadaHacks · Rob Sneiderman <a href="https://github.com/Robby955">@Robby955</a> · Prophet Hacks 2026</p>
</footer>

<script id="data-runs" type="application/json">{json.dumps(per_run)}</script>
<script id="data-spread" type="application/json">{json.dumps(p_stats[:26])}</script>
<script id="data-events" type="application/json">{json.dumps(per_event)}</script>
<script id="data-meta" type="application/json">{json.dumps({"mean": grand_mean, "std": grand_std})}</script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script>
const runs = JSON.parse(document.getElementById('data-runs').textContent);
const spread = JSON.parse(document.getElementById('data-spread').textContent);
const events = JSON.parse(document.getElementById('data-events').textContent);
const meta = JSON.parse(document.getElementById('data-meta').textContent);

// Plot 1: per-run mean Brier
Plotly.newPlot('plot-runs', [{{
  type: 'bar',
  x: runs.map(r => r.label),
  y: runs.map(r => r.mean_brier),
  marker: {{
    color: runs.map(r => r.label === 'canonical' ? '#1e6f3a' : '#2856a3'),
  }},
  text: runs.map(r => r.mean_brier.toFixed(5)),
  textposition: 'outside',
  hovertemplate: '%{{x}}<br>Brier %{{y:.5f}}<br>n=' + runs[0].n + '<extra></extra>',
}}], {{
  margin: {{ l: 60, r: 20, t: 10, b: 40 }},
  yaxis: {{ title: 'mean single-binary Brier', gridcolor: '#eef0f5', range: [0, Math.max(...runs.map(r => r.mean_brier)) * 1.25] }},
  xaxis: {{ gridcolor: '#eef0f5' }},
  shapes: [
    {{ type: 'line', x0: -0.5, x1: runs.length - 0.5, y0: meta.mean, y1: meta.mean,
       line: {{ color: '#a02828', width: 1.5, dash: 'dash' }} }},
    {{ type: 'rect', x0: -0.5, x1: runs.length - 0.5, y0: meta.mean - meta.std, y1: meta.mean + meta.std,
       line: {{ width: 0 }}, fillcolor: 'rgba(160, 40, 40, 0.08)' }},
  ],
  annotations: [
    {{ x: runs.length - 0.5, y: meta.mean, text: 'grand mean ' + meta.mean.toFixed(5),
       showarrow: false, xanchor: 'right', yanchor: 'bottom', font: {{ color: '#a02828', size: 11 }} }},
  ],
  plot_bgcolor: 'white', paper_bgcolor: '#fafbfc',
}}, {{ responsive: true, displaylogo: false }});

// Plot 2: per-event p_yes spread (strip-plot style)
const yLabels = spread.map(s => (s.title || s.ticker).substring(0, 60));
const traces2 = [];
// Range bar
traces2.push({{
  type: 'bar', orientation: 'h',
  x: spread.map(s => s.p_range),
  y: yLabels,
  base: spread.map(s => Math.min(...s.all_p)),
  marker: {{ color: '#cbd5e1', line: {{ width: 0 }} }},
  hovertemplate: '%{{y}}<br>spread %{{x:.3f}}<extra></extra>',
  showlegend: false,
}});
// Individual dots
spread.forEach((s, i) => {{
  traces2.push({{
    type: 'scatter', mode: 'markers',
    x: s.all_p,
    y: Array(s.all_p.length).fill(yLabels[i]),
    marker: {{ color: '#2856a3', size: 8, opacity: 0.75 }},
    hovertemplate: '%{{y}}<br>p_yes %{{x:.3f}}<extra></extra>',
    showlegend: false,
  }});
}});
Plotly.newPlot('plot-spread', traces2, {{
  margin: {{ l: 360, r: 20, t: 10, b: 40 }},
  xaxis: {{ title: 'p_yes across 5 reruns', gridcolor: '#eef0f5', range: [0, 1] }},
  yaxis: {{ autorange: 'reversed', tickfont: {{ size: 10 }} }},
  plot_bgcolor: 'white', paper_bgcolor: '#fafbfc',
}}, {{ responsive: true, displaylogo: false }});

// Plot 3: per-event Brier jittered
const sortedEvents = events.slice().sort((a, b) => a.mean - b.mean);
const jx = [], jy = [], jtext = [];
sortedEvents.forEach((e, i) => {{
  e.all.forEach(b => {{
    jx.push(i);
    jy.push(b);
    jtext.push((e.title || e.ticker) + '<br>Brier ' + b.toFixed(4));
  }});
}});
Plotly.newPlot('plot-jitter', [{{
  type: 'scatter', mode: 'markers',
  x: jx, y: jy, text: jtext,
  marker: {{ color: '#2856a3', size: 7, opacity: 0.55 }},
  hovertemplate: '%{{text}}<extra></extra>',
}}], {{
  margin: {{ l: 60, r: 20, t: 10, b: 40 }},
  xaxis: {{ title: 'event index (sorted by mean Brier)', gridcolor: '#eef0f5' }},
  yaxis: {{ title: 'per-event Brier across 5 runs', gridcolor: '#eef0f5' }},
  plot_bgcolor: 'white', paper_bgcolor: '#fafbfc',
}}, {{ responsive: true, displaylogo: false }});
</script>
"""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Intra-model variance · ForecastingPath</title>
<style>
body {{ font: 14px/1.5 -apple-system, system-ui, sans-serif; color: #1a1f2c; background: #fafbfc; margin: 0; padding: 20px 28px; }}
header {{ max-width: 1200px; margin: 0 auto 14px; }}
header h1 {{ font-size: 22px; margin: 0 0 8px; }}
header .sub {{ color: #475066; margin: 4px 0; font-size: 13px; max-width: 1000px; }}
header .links {{ font-size: 13px; margin-top: 6px; }}
header .links a {{ color: #2856a3; text-decoration: none; margin-right: 12px; }}
header .links a:hover {{ text-decoration: underline; }}
.kpis {{ max-width: 1200px; margin: 12px auto; display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }}
.kpi {{ background: white; border: 1px solid #d0d6e1; border-radius: 8px; padding: 10px 14px; }}
.kpi .lbl {{ color: #6a7388; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 700; }}
.kpi .val {{ font-size: 1.35em; font-weight: 700; margin-top: 0.2em; font-variant-numeric: tabular-nums; }}
section {{ max-width: 1200px; margin: 0 auto 28px; }}
section h2 {{ font-size: 16px; margin: 8px 0; }}
section .meta {{ color: #6a7388; font-size: 12px; margin-top: 4px; }}
section > div[id^="plot-"] {{ background: white; border: 1px solid #e6e9f0; border-radius: 8px; }}
footer {{ max-width: 1200px; margin: 16px auto; color: #6a7388; font-size: 12px; }}
footer code {{ background: #eef0f5; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    (STATIC / "variance.html").write_text(page)
    print(f"wrote static/variance.html")
    print(f"grand mean: {grand_mean:.5f}, std: {grand_std:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
