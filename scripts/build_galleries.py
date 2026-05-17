#!/usr/bin/env python3
"""Build side-by-side prediction galleries from the cached ablation data.

Produces two standalone HTML pages under static/:

- static/gallery_resolved.html — 26 resolved events, 5 model columns,
  Brier per cell, sortable, searchable, "who got it right" highlights.
- static/gallery_open.html — 42 open events across 3 PA datasets,
  4 model columns (no actuals/Brier column).

Re-run anytime the underlying JSON files change. The output is plain
static HTML — no JS framework, no server-side rendering. Drops straight
into static/ for /static/* serving via FastAPI.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"
STATIC = REPO / "static"

# Display name, file path. Production gets a distinct color.
RESOLVED_MODELS: list[tuple[str, str, Path]] = [
    ("Opus 4.7", "production", PRED / "multi_outcome_retrieval.json"),
    ("Opus 4.6", "alt", PRED / "ablation_claude-opus-4-6.json"),
    ("GPT-5.2", "alt", PRED / "ablation_gpt-5-2.json"),
    ("GPT-5.5", "alt", PRED / "ablation_gpt-5-5.json"),
    ("Gemini 3.1 Pro", "alt", PRED / "ablation_gemini-3-1-pro-preview.json"),
]

OPEN_DATASETS: list[tuple[str, Path]] = [
    ("Economics", DATA / "sample_economics.json"),
    ("Entertainment", DATA / "sample_entertainment.json"),
    ("Sports", DATA / "sample_sports.json"),
]

OPEN_MODELS: list[tuple[str, str, str]] = [
    # (display, kind, slug-in-filename)
    ("Sonnet 4.6", "alt", "claude-sonnet-4-6"),
    ("Opus 4.6", "alt", "claude-opus-4-6"),
    ("GPT-5.2", "alt", "gpt-5-2"),
]


# ---- shared helpers ----------------------------------------------------------


def load_predictions(path: Path) -> dict[str, dict[str, Any]]:
    """Return ticker → row map for a prediction submission JSON."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    rows = raw.get("predictions") if isinstance(raw, dict) else raw
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        t = row.get("market_ticker") or row.get("event_ticker")
        if t:
            out[t] = row
    return out


def brier_single_binary(p_yes: float, outcome: float) -> float:
    """Match prophet forecast evaluate's metric."""
    return (float(p_yes) - float(outcome)) ** 2


def brier_color(b: float) -> str:
    """Map Brier in [0, 1] to a soft red→green CSS color string."""
    if b < 0.02:
        return "#bbe5b0"  # very green
    if b < 0.08:
        return "#d8efc7"
    if b < 0.16:
        return "#f6f4c5"
    if b < 0.30:
        return "#fad9b3"
    return "#f5b7b1"  # red


def category_pill(cat: str) -> str:
    cls = "pill-" + (cat or "other").lower().replace(" ", "-")
    return f'<span class="pill {cls}">{html.escape(cat or "—")}</span>'


def truncate(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ---- resolved gallery --------------------------------------------------------


def build_resolved() -> str:
    events = json.loads((DATA / "resolved.json").read_text())
    actuals = json.loads((DATA / "actuals.json").read_text())
    model_preds = {name: load_predictions(p) for name, _, p in RESOLVED_MODELS}

    # Per-model running totals for the footer.
    totals: dict[str, list[float]] = defaultdict(list)
    rows_html: list[str] = []

    # Sort events by category then title for stable scrolling.
    events_sorted = sorted(events, key=lambda e: (e.get("category", ""), e.get("title", "")))

    for ev in events_sorted:
        ticker = ev.get("market_ticker") or ev.get("event_ticker")
        title = ev.get("title", "")
        cat = ev.get("category", "")
        outcomes = ev.get("outcomes") or []
        n_out = len(outcomes)
        ro = ev.get("resolved_outcome") or {}
        ro_val = ro.get("value") if isinstance(ro, dict) else ro
        if isinstance(ro_val, list):
            resolved_outcome = ", ".join(str(x) for x in ro_val)
        else:
            resolved_outcome = str(ro_val or "—")
        actual = actuals.get(ticker)

        cells: list[str] = []
        per_event_briers: list[tuple[str, float]] = []

        for name, kind, _ in RESOLVED_MODELS:
            row = model_preds[name].get(ticker)
            if not row or actual is None:
                cells.append(f'<td class="missing">—</td>')
                continue
            p_yes = float(row.get("p_yes", 0.5))
            b = brier_single_binary(p_yes, actual)
            totals[name].append(b)
            per_event_briers.append((name, b))
            color = brier_color(b)
            kind_cls = "prod-cell" if kind == "production" else ""
            cells.append(
                f'<td class="brier-cell {kind_cls}" style="background:{color}" '
                f'title="p_yes={p_yes:.3f} · actual={int(actual)} · Brier={b:.4f}">'
                f'<span class="p">{p_yes:.2f}</span>'
                f'<span class="b">B={b:.3f}</span>'
                f'</td>'
            )

        # "who won this event" — lowest Brier wins; tie if within 0.001.
        winner_name = ""
        if per_event_briers:
            best_b = min(b for _, b in per_event_briers)
            tied = [n for n, b in per_event_briers if abs(b - best_b) < 0.001]
            winner_name = "tie" if len(tied) == len(per_event_briers) else (
                "tie (" + ", ".join(tied) + ")" if len(tied) > 1 else tied[0]
            )

        rows_html.append(
            f'<tr data-category="{html.escape(cat)}" data-title="{html.escape(title.lower())}">'
            f'<td class="qcol"><div class="qtitle">{html.escape(truncate(title, 110))}</div>'
            f'<div class="qmeta">{category_pill(cat)} · n_outcomes={n_out} · winner: '
            f'<strong>{html.escape(truncate(resolved_outcome, 60))}</strong></div></td>'
            f'<td class="winnercol">{html.escape(winner_name)}</td>'
            + "".join(cells)
            + "</tr>"
        )

    # Footer row: mean per model.
    footer_cells: list[str] = []
    for name, kind, _ in RESOLVED_MODELS:
        if not totals[name]:
            footer_cells.append("<td>—</td>")
            continue
        mean = sum(totals[name]) / len(totals[name])
        color = brier_color(mean)
        kind_cls = "prod-cell" if kind == "production" else ""
        footer_cells.append(
            f'<td class="brier-cell {kind_cls}" style="background:{color}">'
            f'<strong>{mean:.4f}</strong><span class="b">mean</span></td>'
        )

    model_header = "".join(
        f'<th class="modelcol {"prod-col" if k=="production" else ""}">{html.escape(n)}'
        + (' <span class="prod-tag">production</span>' if k == "production" else "")
        + "</th>"
        for n, k, _ in RESOLVED_MODELS
    )

    body = f"""
<header>
  <h1>Side-by-side gallery · resolved events</h1>
  <p class="sub">26 events from PA's <code>sample-resolved</code> set, same retrieval + prompt + longshot floor, five LLM swaps. Each cell shows model's <em>p(outcomes[0] wins)</em> and the resulting single-binary Brier (Prophet Arena CLI metric). Color: green = low loss, red = high loss. Footer row is the mean Brier per model.</p>
  <p class="links">
    <a href="/static/gallery_open.html">→ open events gallery (no actuals)</a> ·
    <a href="/static/summary.html">→ summary report</a> ·
    <a href="/">→ home</a>
  </p>
  <div class="controls">
    <input type="search" id="filter" placeholder="filter by question text…" />
    <select id="cat">
      <option value="">all categories</option>
      <option>Sports</option>
      <option>Entertainment</option>
      <option>Elections</option>
      <option>Politics</option>
    </select>
  </div>
</header>
<table class="gallery">
  <thead>
    <tr>
      <th class="qcol">event</th>
      <th class="winnercol">best Brier</th>
      {model_header}
    </tr>
  </thead>
  <tbody>
    {"".join(rows_html)}
  </tbody>
  <tfoot>
    <tr><td><strong>mean (single-binary Brier ↓)</strong></td><td>—</td>{"".join(footer_cells)}</tr>
  </tfoot>
</table>
<footer>
  <p>Built by <code>scripts/build_galleries.py</code>. Source: <code>data/resolved.json</code> + <code>data/actuals.json</code> + <code>data/predictions/multi_outcome_retrieval.json</code> + <code>data/predictions/ablation_*.json</code>. Brier is single-binary (PA CLI metric); multi-class numbers in <a href="/static/summary.html">summary report</a>.</p>
</footer>
"""
    return _wrap_html("Side-by-side gallery · resolved", body)


# ---- open gallery ------------------------------------------------------------


def build_open() -> str:
    sections: list[str] = []
    for ds_name, ds_path in OPEN_DATASETS:
        if not ds_path.exists():
            continue
        events = json.loads(ds_path.read_text())
        events_list = events if isinstance(events, list) else events.get("events", [])

        # Load 3 models for this dataset slug.
        ds_slug = "sample-" + ds_name.lower()
        model_preds = {
            display: load_predictions(PRED / f"ablation_open_{ds_slug}_{slug}.json")
            for display, _, slug in OPEN_MODELS
        }

        rows_html: list[str] = []
        for ev in sorted(events_list, key=lambda e: e.get("title", "")):
            ticker = ev.get("market_ticker") or ev.get("event_ticker")
            title = ev.get("title", "")
            outcomes = ev.get("outcomes") or []
            outcome0 = outcomes[0] if outcomes else "?"

            cells: list[str] = []
            for display, _, _ in OPEN_MODELS:
                row = model_preds[display].get(ticker)
                if not row:
                    cells.append('<td class="missing">—</td>')
                    continue
                p_yes = float(row.get("p_yes", 0.5))
                # Color cell by confidence (distance from 0.5).
                conf = abs(p_yes - 0.5) * 2
                shade = int(255 - conf * 100)
                cells.append(
                    f'<td class="p-cell" style="background:rgb({shade},{shade+8},{shade})" '
                    f'title="p({html.escape(outcome0)})={p_yes:.3f}">'
                    f'<span class="p">{p_yes:.2f}</span>'
                    f'</td>'
                )

            # Cross-model spread on p(outcome[0]) — surfacing disagreement.
            ps = [float(model_preds[d].get(ticker, {}).get("p_yes", float("nan")))
                  for d, _, _ in OPEN_MODELS]
            ps_valid = [p for p in ps if p == p]
            spread = max(ps_valid) - min(ps_valid) if len(ps_valid) >= 2 else 0.0
            spread_color = "#f5b7b1" if spread > 0.3 else "#fad9b3" if spread > 0.15 else "#d8efc7"

            rows_html.append(
                f'<tr><td class="qcol"><div class="qtitle">{html.escape(truncate(title, 110))}</div>'
                f'<div class="qmeta">n_outcomes={len(outcomes)} · p shown is for: <strong>{html.escape(truncate(outcome0, 60))}</strong></div></td>'
                f'<td class="spread-cell" style="background:{spread_color}">{spread:.2f}</td>'
                + "".join(cells)
                + "</tr>"
            )

        model_header = "".join(
            f'<th class="modelcol">{html.escape(d)}</th>' for d, _, _ in OPEN_MODELS
        )
        sections.append(f"""
<section>
<h2>{html.escape(ds_name)} · {len(events_list)} open events</h2>
<table class="gallery">
  <thead>
    <tr>
      <th class="qcol">event</th>
      <th class="spread-cell">cross-model spread</th>
      {model_header}
    </tr>
  </thead>
  <tbody>{"".join(rows_html)}</tbody>
</table>
</section>
""")

    body = f"""
<header>
  <h1>Side-by-side gallery · open events</h1>
  <p class="sub">42 unresolved events across PA's three open sample datasets (Economics, Entertainment, Sports). Same retrieval + prompt as production, three alternative LLMs swapped in. No actuals yet — instead the <em>cross-model spread</em> column surfaces where models disagree (red = high spread, useful as a triage signal when live events arrive).</p>
  <p class="links">
    <a href="/static/gallery_resolved.html">→ resolved events gallery</a> ·
    <a href="/static/summary.html">→ summary report</a> ·
    <a href="/">→ home</a>
  </p>
</header>
{"".join(sections)}
<footer>
  <p>Built by <code>scripts/build_galleries.py</code>. Sources: <code>data/sample_*.json</code> + <code>data/predictions/ablation_open_*.json</code>. Probabilities shown are model output for <em>outcomes[0]</em> (PA's "outcome of interest" convention).</p>
</footer>
"""
    return _wrap_html("Side-by-side gallery · open", body)


# ---- shared layout -----------------------------------------------------------


def _wrap_html(title: str, body: str) -> str:
    css = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font: 14px/1.45 system-ui, -apple-system, sans-serif; color: #1a1f2c; background: #fafbfc; margin: 0; padding: 24px 32px; }
header { max-width: 1400px; margin: 0 auto 18px; }
header h1 { font-size: 24px; margin: 0 0 4px; }
header .sub { color: #475066; margin: 4px 0 8px; max-width: 900px; }
header .links { font-size: 13px; margin-top: 6px; }
header .links a { color: #2856a3; text-decoration: none; margin-right: 12px; }
header .links a:hover { text-decoration: underline; }
.controls { margin: 12px 0; display: flex; gap: 8px; }
.controls input, .controls select { font: 14px system-ui; padding: 6px 10px; border: 1px solid #c8cdd9; border-radius: 6px; background: white; }
.controls input { width: 360px; }
table.gallery { width: 100%; max-width: 1400px; margin: 0 auto; border-collapse: separate; border-spacing: 0; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-radius: 8px; overflow: hidden; }
table.gallery thead th { position: sticky; top: 0; background: #f4f6fa; padding: 10px 12px; text-align: left; border-bottom: 2px solid #d0d6e1; font-weight: 600; z-index: 2; }
table.gallery tbody td, table.gallery tfoot td { padding: 8px 12px; border-bottom: 1px solid #eef0f5; vertical-align: top; }
table.gallery tbody tr:hover { background: #f9faff; }
table.gallery .qcol { min-width: 360px; max-width: 460px; }
table.gallery .qtitle { font-weight: 500; }
table.gallery .qmeta { color: #6a7388; font-size: 12px; margin-top: 2px; }
table.gallery .winnercol { font-weight: 600; color: #2856a3; min-width: 90px; }
table.gallery .modelcol { text-align: center; min-width: 100px; }
table.gallery .prod-col { color: #1e6f3a; }
.prod-tag { font-size: 10px; background: #1e6f3a; color: white; padding: 1px 5px; border-radius: 3px; vertical-align: middle; margin-left: 3px; }
table.gallery .brier-cell, table.gallery .p-cell { text-align: center; font-variant-numeric: tabular-nums; }
table.gallery .brier-cell span.p { display: block; font-weight: 600; }
table.gallery .brier-cell span.b { display: block; color: #475066; font-size: 11px; }
table.gallery .brier-cell.prod-cell { box-shadow: inset 0 0 0 2px rgba(30, 111, 58, 0.4); }
table.gallery .spread-cell { text-align: center; font-weight: 600; font-variant-numeric: tabular-nums; min-width: 80px; }
table.gallery .missing { text-align: center; color: #b0b7c4; }
table.gallery tfoot td { background: #f4f6fa; border-top: 2px solid #d0d6e1; font-size: 13px; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; background: #e0e5ee; color: #475066; }
.pill-sports { background: #d9eafa; color: #2856a3; }
.pill-entertainment { background: #f0e0fa; color: #6b2e9c; }
.pill-elections { background: #fae0d9; color: #a04020; }
.pill-politics { background: #faead9; color: #8a5a18; }
section { max-width: 1400px; margin: 0 auto 28px; }
section h2 { font-size: 17px; margin: 16px 0 10px; padding-bottom: 6px; border-bottom: 1px solid #d0d6e1; max-width: 1400px; margin-left: auto; margin-right: auto; }
footer { max-width: 1400px; margin: 16px auto; color: #6a7388; font-size: 12px; }
footer code { background: #eef0f5; padding: 1px 4px; border-radius: 3px; font-size: 11px; }
"""
    js = """
const filter = document.getElementById('filter');
const cat = document.getElementById('cat');
function apply() {
  if (!filter && !cat) return;
  const q = (filter?.value || '').toLowerCase().trim();
  const c = cat?.value || '';
  document.querySelectorAll('table.gallery tbody tr').forEach(r => {
    const t = r.dataset.title || '';
    const rc = r.dataset.category || '';
    const matchQ = !q || t.includes(q);
    const matchC = !c || rc === c;
    r.style.display = (matchQ && matchC) ? '' : 'none';
  });
}
filter?.addEventListener('input', apply);
cat?.addEventListener('change', apply);
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · ForecastingPath</title>
<style>{css}</style>
</head>
<body>
{body}
<script>{js}</script>
</body>
</html>
"""


# ---- main --------------------------------------------------------------------


def main() -> int:
    STATIC.mkdir(exist_ok=True)
    (STATIC / "gallery_resolved.html").write_text(build_resolved())
    (STATIC / "gallery_open.html").write_text(build_open())
    print("wrote static/gallery_resolved.html")
    print("wrote static/gallery_open.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
