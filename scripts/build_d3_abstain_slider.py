#!/usr/bin/env python3
"""D3: abstain-to-market policy slider — interactive page.

Demonstrates PA's scoring rule visually. Two policies:

  Always trade:     return our model prediction for every event.
  Abstain-to-market: if |p_model − 0.5| < threshold, return 0.5
                     (uniform / market-baseline-equivalent on binary).
                     Otherwise return p_model.

The slider varies the abstain threshold from 0 (always trade) to 0.5
(always abstain to 0.5). For each threshold the page recomputes:

  - mean Brier of the policy on n=26
  - n events where we "abstain"
  - n events where we trade and beat 0.25 (random baseline)

For a real PA evaluation the abstain fallback would be the LIVE
market price, not 0.5 — that's why this is a stand-in. The shape of
the curve still demonstrates the strategic tradeoff: abstain too
much and you stop adding edge; abstain too little and you lose on
unconfident bets.
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
    actuals = json.load(open(DATA / "actuals.json"))
    prod = json.load(open(PRED / "multi_outcome_retrieval.json"))
    rows = prod.get("predictions", prod)

    pairs = []
    events = {e["market_ticker"]: e for e in json.load(open(DATA / "resolved.json"))}
    for r in rows:
        t = r["market_ticker"]
        a = actuals.get(t)
        if a is None:
            continue
        ev = events.get(t, {})
        pairs.append({
            "ticker": t,
            "title": ev.get("title", "")[:80],
            "p": float(r["p_yes"]),
            "outcome": int(a),
        })

    pairs.sort(key=lambda d: d["p"])
    data_json = json.dumps(pairs)

    body = f"""
<header>
  <h1>Abstain-to-market policy slider</h1>
  <p class="sub">PA scoring rule: <strong>(team Brier − market Brier) × completion rate</strong>. Jibang Wu (PA, Discord 2026-05-16): "<em>only make a prediction when you are confident enough, otherwise just use the market probability.</em>"</p>
  <p class="sub">This page visualizes that policy on our 26 resolved events. <strong>Slide the threshold</strong> to control how confident the model has to be (distance from 0.5) before we trade. Below threshold → fall back to 0.5 (proxy for market price; PA-live this would be the actual snapshotted market). The chart updates live.</p>
  <p class="links">
    <a href="/static/scatter_resolved.html">→ per-event scatter</a> ·
    <a href="/static/gallery_resolved.html">→ side-by-side gallery</a> ·
    <a href="/static/summary.html">→ summary report</a> ·
    <a href="/">→ home</a>
  </p>
</header>

<div class="control-row">
  <label for="threshold">Confidence threshold <span class="hint">|p − 0.5| ≥</span></label>
  <input type="range" id="threshold" min="0" max="0.49" step="0.01" value="0.10"/>
  <output for="threshold" id="threshold-val">0.10</output>
</div>

<div class="kpis">
  <div class="kpi"><div class="lbl">Mean Brier (policy)</div><div class="val" id="kpi-brier">—</div></div>
  <div class="kpi"><div class="lbl">Δ vs always-trade</div><div class="val" id="kpi-delta">—</div></div>
  <div class="kpi"><div class="lbl">Events traded</div><div class="val" id="kpi-traded">—</div></div>
  <div class="kpi"><div class="lbl">Events abstained</div><div class="val" id="kpi-abstained">—</div></div>
</div>

<div id="plot" style="height: 480px;"></div>

<div class="event-list" id="event-list"></div>

<footer>
  <p>Built by <code>scripts/build_d3_abstain_slider.py</code>. Source: <code>data/predictions/multi_outcome_retrieval.json</code> + <code>data/actuals.json</code>. Brier = single-binary; abstain fallback is 0.5 as a proxy for "the live market" (PA-live this is snapshotted Kalshi/Polymarket prices). Slide to see how policy choice maps to expected score.</p>
</footer>

<script id="pairs-data" type="application/json">{data_json}</script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script>
const pairs = JSON.parse(document.getElementById('pairs-data').textContent);
const slider = document.getElementById('threshold');
const out = document.getElementById('threshold-val');
const eventListEl = document.getElementById('event-list');

function policyBrier(thr) {{
  const traded = [];
  const abstained = [];
  let lossSum = 0;
  for (const p of pairs) {{
    const conf = Math.abs(p.p - 0.5);
    const used_p = conf >= thr ? p.p : 0.5;
    const loss = (used_p - p.outcome) ** 2;
    lossSum += loss;
    (conf >= thr ? traded : abstained).push({{...p, used_p, loss}});
  }}
  return {{
    mean: lossSum / pairs.length,
    traded, abstained,
  }};
}}

const baseline = policyBrier(0).mean;  // always-trade

let plot = null;
function render(thr) {{
  out.textContent = thr.toFixed(2);
  const r = policyBrier(thr);
  document.getElementById('kpi-brier').textContent = r.mean.toFixed(5);
  const delta = r.mean - baseline;
  document.getElementById('kpi-delta').textContent = (delta >= 0 ? '+' : '') + delta.toFixed(5);
  document.getElementById('kpi-delta').style.color = delta < 0 ? '#1e6f3a' : (delta > 0.0005 ? '#a02828' : '#475066');
  document.getElementById('kpi-traded').textContent = r.traded.length;
  document.getElementById('kpi-abstained').textContent = r.abstained.length;

  // Plot: sweep threshold from 0 to 0.49 and plot policy Brier curve
  const xs = [];
  const ys = [];
  for (let t = 0; t <= 0.49; t += 0.01) {{
    xs.push(t);
    ys.push(policyBrier(t).mean);
  }}
  const tracesLine = [{{
    x: xs, y: ys, mode: 'lines', type: 'scatter',
    line: {{ color: '#2856a3', width: 2 }},
    name: 'mean Brier under policy',
    hovertemplate: 'threshold=%{{x:.2f}}<br>Brier=%{{y:.5f}}<extra></extra>',
  }}, {{
    x: [thr], y: [r.mean], mode: 'markers', type: 'scatter',
    marker: {{ color: '#a02828', size: 12, symbol: 'diamond' }},
    name: `current (thr=${{thr.toFixed(2)}})`,
    hoverinfo: 'skip',
  }}, {{
    x: [0, 0.49], y: [baseline, baseline], mode: 'lines', type: 'scatter',
    line: {{ color: '#b0b7c4', dash: 'dot', width: 1 }},
    name: 'always-trade baseline',
    hoverinfo: 'skip',
  }}];
  const layout = {{
    margin: {{ l: 60, r: 20, t: 10, b: 50 }},
    xaxis: {{ title: 'abstain threshold |p_model − 0.5|', gridcolor: '#eef0f5' }},
    yaxis: {{ title: 'mean Brier (policy on n=26)', gridcolor: '#eef0f5' }},
    legend: {{ x: 0.55, y: 0.98, bgcolor: 'rgba(255,255,255,0.85)' }},
    plot_bgcolor: 'white',
    paper_bgcolor: '#fafbfc',
  }};
  if (!plot) {{
    Plotly.newPlot('plot', tracesLine, layout, {{ responsive: true, displaylogo: false }});
    plot = true;
  }} else {{
    Plotly.react('plot', tracesLine, layout, {{ responsive: true, displaylogo: false }});
  }}

  // Event list
  let html_str = '<table class="event-tbl"><thead><tr><th>event</th><th>p_model</th><th>actual</th><th>action</th><th>used p</th><th>loss</th></tr></thead><tbody>';
  const all = [...r.traded.map(e => ({{...e, action: 'trade'}})), ...r.abstained.map(e => ({{...e, action: 'abstain'}}))]
    .sort((a, b) => b.loss - a.loss);
  for (const e of all) {{
    const actCls = e.action === 'trade' ? 'act-trade' : 'act-abstain';
    html_str += `<tr><td class="ev-title">${{escapeHtml(e.title)}}</td>` +
                `<td>${{e.p.toFixed(2)}}</td>` +
                `<td>${{e.outcome}}</td>` +
                `<td class="${{actCls}}">${{e.action}}</td>` +
                `<td>${{e.used_p.toFixed(2)}}</td>` +
                `<td>${{e.loss.toFixed(4)}}</td></tr>`;
  }}
  html_str += '</tbody></table>';
  eventListEl.innerHTML = html_str;
}}
function escapeHtml(s) {{ return s.replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
slider.addEventListener('input', () => render(parseFloat(slider.value)));
render(parseFloat(slider.value));
</script>
"""

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Abstain-to-market slider · ForecastingPath</title>
<style>
body {{ font: 14px/1.45 -apple-system, system-ui, sans-serif; color: #1a1f2c; background: #fafbfc; margin: 0; padding: 20px 28px; }}
header {{ max-width: 1100px; margin: 0 auto 14px; }}
header h1 {{ font-size: 22px; margin: 0 0 6px; }}
header .sub {{ color: #475066; margin: 4px 0; font-size: 13px; max-width: 900px; }}
header .links {{ font-size: 13px; margin-top: 6px; }}
header .links a {{ color: #2856a3; text-decoration: none; margin-right: 12px; }}
.control-row {{ max-width: 1100px; margin: 12px auto; display: flex; align-items: center; gap: 16px; background: white; padding: 14px 18px; border: 1px solid #d0d6e1; border-radius: 8px; }}
.control-row label {{ font-weight: 600; font-size: 13px; }}
.control-row label .hint {{ color: #6a7388; font-weight: 400; margin-left: 4px; }}
.control-row input[type="range"] {{ flex: 1; }}
.control-row output {{ font-variant-numeric: tabular-nums; font-weight: 600; min-width: 50px; color: #2856a3; }}
.kpis {{ max-width: 1100px; margin: 8px auto 12px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }}
.kpi {{ background: white; border: 1px solid #d0d6e1; border-radius: 8px; padding: 10px 14px; }}
.kpi .lbl {{ color: #6a7388; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 700; }}
.kpi .val {{ font-size: 1.35em; font-weight: 700; margin-top: 0.2em; font-variant-numeric: tabular-nums; }}
#plot {{ max-width: 1100px; margin: 0 auto; background: white; border: 1px solid #e6e9f0; border-radius: 8px; }}
.event-list {{ max-width: 1100px; margin: 12px auto; }}
.event-tbl {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); font-variant-numeric: tabular-nums; }}
.event-tbl thead th {{ background: #f4f6fa; padding: 8px 12px; text-align: left; font-size: 12px; text-transform: uppercase; color: #475066; }}
.event-tbl tbody td {{ padding: 6px 12px; border-bottom: 1px solid #eef0f5; font-size: 13px; }}
.event-tbl .ev-title {{ max-width: 480px; }}
.event-tbl .act-trade {{ color: #1e6f3a; font-weight: 600; }}
.event-tbl .act-abstain {{ color: #6a7388; font-style: italic; }}
footer {{ max-width: 1100px; margin: 12px auto; color: #6a7388; font-size: 12px; }}
footer code {{ background: #eef0f5; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    (STATIC / "abstain_slider.html").write_text(html_out)
    print(f"wrote static/abstain_slider.html ({len(pairs)} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
