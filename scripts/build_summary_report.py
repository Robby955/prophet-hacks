"""Build a one-page summary report of the ForecastingPath agent.

Reads the on-disk prediction files + DECISIONS.md + commit history,
renders both:

  static/summary.html  — beautiful single-file HTML (browseable, printable)
  static/summary.pdf   — matplotlib-rendered multi-panel PDF (downloadable)

Both ship in the deploy and are linkable from the public landing.

Usage:
    python scripts/build_summary_report.py
    python scripts/build_summary_report.py --out static/

Designed to be cheap (no API calls) and idempotent (rebuild any time).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Use a non-interactive matplotlib backend so this runs in headless deploys.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent

# Mapping label -> (filename in data/predictions/, color for chart)
ABLATION_FILES = [
    ("Opus 4.7 (production)",      "multi_outcome_retrieval.json",                       "#1d4ed8"),
    ("Sonnet 4.6 (prev prod)",     "multi_outcome_retrieval.phase1_sonnet.json",          "#0891b2"),
    ("Opus 4.6",                   "ablation_claude-opus-4-6.json",                       "#7c3aed"),
    ("GPT-5.5",                    "ablation_gpt-5-5.json",                               "#0d9488"),
    ("GPT-5.2",                    "ablation_gpt-5-2.json",                               "#16a34a"),
    ("Gemini 3.1 Pro (post-harden)", "ablation_gemini-3-1-pro-preview-postharden.json",   "#ea580c"),
]

# Statistical-significance numbers from the paired-bootstrap on the same
# 26 events (Codex PR #6, scripts/bootstrap_brier_ci.py). Hard-coded
# because the bootstrap script is in a separate branch right now; pull
# from JSON output once it lands on main.
BOOTSTRAP_CI = {
    "mean_delta": 0.026027,
    "ci_low":     0.014270,
    "ci_high":    0.037373,
    "n_events":   26,
    "n_resamples": 50000,
    "seed":       20260516,
}
# Headline decomposition (Codex PR #6): Sonnet 4.6 run with the NEW floor
# formula scored 0.041838; production Opus 4.7 scored 0.037912.
# So the floor-bug fix accounts for ~85% of the 0.0639 → 0.0379 gap;
# the model swap accounts for ~15%.
DECOMPOSITION = {
    "phase1_sonnet_old_floor": 0.063939,
    "sonnet_new_floor":        0.041838,
    "phase2_opus_new_floor":   0.037912,
}

# Open-event ablations Codex ran. No actuals (events unresolved), so we
# compute model-agreement statistics instead of Brier.
OPEN_DATASETS = ["sample-economics", "sample-entertainment", "sample-sports"]
OPEN_MODELS = [
    ("Opus 4.7 (prod)",  "open_{ds}.json"),
    ("Opus 4.6",         "ablation_open_{ds}_claude-opus-4-6.json"),
    ("Sonnet 4.6",       "ablation_open_{ds}_claude-sonnet-4-6.json"),
    ("GPT-5.2",          "ablation_open_{ds}_gpt-5-2.json"),
]


def _load_predictions(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    preds = raw.get("predictions", raw) if isinstance(raw, dict) else raw
    return {p.get("market_ticker"): p for p in preds if p.get("market_ticker")}


def _multi_brier(probs: list[float], winner_idx: int) -> float:
    return sum((p - (1.0 if i == winner_idx else 0.0)) ** 2 for i, p in enumerate(probs))


def _compute_open_agreement() -> dict:
    """Per-open-dataset model agreement stats.

    For each event, compute the spread (max - min) of p(outcome[0])
    across the 4 models. Aggregate into:
      - n_consensus: events where spread < 0.10 (all models close)
      - n_contested: events where spread > 0.30 (high disagreement)
      - n_mid: in between
      - mean_spread: average spread across the dataset
    """
    pred_dir = ROOT / "data/predictions"
    out: dict[str, dict] = {}
    for ds in OPEN_DATASETS:
        per_event_p0: dict[str, list[float]] = {}
        for label, fname_tmpl in OPEN_MODELS:
            p = pred_dir / fname_tmpl.format(ds=ds)
            if not p.exists():
                continue
            data = json.loads(p.read_text())
            preds = data.get("predictions", []) if isinstance(data, dict) else data
            for pr in preds:
                t = pr.get("market_ticker") or pr.get("_event", {}).get("market_ticker")
                if not t:
                    continue
                p0 = pr.get("p_yes")
                if p0 is None:
                    probs = pr.get("probabilities", [])
                    p0 = probs[0]["probability"] if probs else None
                if p0 is not None:
                    per_event_p0.setdefault(t, []).append(float(p0))
        # Aggregate spread stats
        spreads = []
        n_consensus = n_mid = n_contested = 0
        for t, vals in per_event_p0.items():
            if len(vals) < 2:
                continue
            spread = max(vals) - min(vals)
            spreads.append(spread)
            if spread < 0.10:
                n_consensus += 1
            elif spread > 0.30:
                n_contested += 1
            else:
                n_mid += 1
        out[ds] = {
            "n_events": len(per_event_p0),
            "n_with_multi_model": len(spreads),
            "mean_spread": sum(spreads) / len(spreads) if spreads else 0.0,
            "n_consensus": n_consensus,
            "n_mid": n_mid,
            "n_contested": n_contested,
        }
    return out


def _compute_summary() -> dict[str, Any]:
    resolved = json.loads((ROOT / "data/resolved.json").read_text())
    actuals = json.loads((ROOT / "data/actuals.json").read_text())
    by_ticker = {e["market_ticker"]: e for e in resolved}

    per_model: dict[str, dict[str, Any]] = {}
    for label, fname, color in ABLATION_FILES:
        preds = _load_predictions(ROOT / "data/predictions" / fname)
        endpoint = []
        binary, multi = [], []
        binary_actual, multi_actual = [], []
        binary_p = []  # for reliability diagram (production model only)

        for ticker, ev in by_ticker.items():
            if ticker not in preds:
                continue
            outs = ev.get("outcomes") or []
            ro = ev.get("resolved_outcome") or {}
            winner_list = ro.get("value") if isinstance(ro, dict) else None
            winner = winner_list[0] if winner_list else None
            if not outs or winner not in outs:
                continue
            wi = outs.index(winner)
            p = preds[ticker]
            if "p_yes" in p and ticker in actuals:
                endpoint.append((float(p["p_yes"]) - float(actuals[ticker])) ** 2)
            if len(outs) == 2 and "p_yes" in p:
                py = p["p_yes"]
                actual = 1 if wi == 0 else 0
                binary.append((py - actual) ** 2)
                binary_actual.append(actual)
                binary_p.append(py)
            elif p.get("probabilities"):
                pmap = {pp.get("market"): pp.get("probability", 0.0)
                        for pp in p.get("probabilities", [])}
                vec = [pmap.get(o, 1.0 / len(outs)) for o in outs]
                multi.append(_multi_brier(vec, wi))

        per_model[label] = {
            "color": color,
            "mean_brier": (sum(endpoint) / len(endpoint))
                          if endpoint else (
                              (sum(binary + multi) / len(binary + multi))
                              if (binary or multi) else None
                          ),
            "binary_mean": sum(binary) / len(binary) if binary else None,
            "multi_mean": sum(multi) / len(multi) if multi else None,
            "n_endpoint": len(endpoint),
            "n_binary": len(binary),
            "n_multi": len(multi),
            "endpoint_briers": endpoint,
            "binary_briers": binary,
            "binary_p": binary_p,
            "binary_actual": binary_actual,
        }

    # Open-event multi-model agreement (Codex's offline ablations)
    open_summary = _compute_open_agreement()

    git_sha = "unknown"
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=ROOT, text=True, timeout=2,
        ).strip()
    except Exception:
        pass

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "n_events_resolved": len(resolved),
        "per_model": per_model,
        "baselines": {"random_binary": 0.250, "uniform_prior": 0.219},
        "open_summary": open_summary,
    }


# --- HTML rendering -------------------------------------------------------


def _open_rows(s: dict[str, Any]) -> list[str]:
    out = []
    for ds, stats in s.get("open_summary", {}).items():
        if stats["n_events"] == 0:
            continue
        out.append(
            f"<tr><td><strong>{ds}</strong></td>"
            f"<td>{stats['n_events']}</td>"
            f"<td>{stats['mean_spread']:.3f}</td>"
            f"<td style='color:#047857'>{stats['n_consensus']}</td>"
            f"<td>{stats['n_mid']}</td>"
            f"<td style='color:#b91c1c'>{stats['n_contested']}</td></tr>"
        )
    return out


def _render_html(s: dict[str, Any]) -> str:
    rows = []
    for label, m in s["per_model"].items():
        mb = m["mean_brier"]
        bm = m["binary_mean"]
        mm = m["multi_mean"]
        rows.append(f"""<tr>
            <td><span class="dot" style="background:{m['color']}"></span><strong>{label}</strong></td>
            <td>{f"{mb:.4f}" if mb is not None else '—'}</td>
            <td>{f"{bm:.4f}" if bm is not None else '—'}</td>
            <td>{f"{mm:.4f}" if mm is not None else '—'}</td>
            <td>{m['n_endpoint']}</td>
        </tr>""")

    # Decomposition + bootstrap numbers (pulled in here so they're in scope
    # of the f-string body below).
    phase1_sonnet_old_floor = DECOMPOSITION["phase1_sonnet_old_floor"]
    sonnet_new_floor = DECOMPOSITION["sonnet_new_floor"]
    phase2_opus_new_floor = DECOMPOSITION["phase2_opus_new_floor"]
    delta_total = phase1_sonnet_old_floor - phase2_opus_new_floor
    delta_floor_only = phase1_sonnet_old_floor - sonnet_new_floor
    pct_floor_share = 100.0 * delta_floor_only / delta_total if delta_total else 0.0
    boot_mean = BOOTSTRAP_CI["mean_delta"]
    boot_lo = BOOTSTRAP_CI["ci_low"]
    boot_hi = BOOTSTRAP_CI["ci_high"]
    boot_n_resamples = BOOTSTRAP_CI["n_resamples"]
    boot_seed = BOOTSTRAP_CI["seed"]

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>ForecastingPath · Summary Report</title>
<style>
  :root {{
    --bg: #fafafa; --panel: #fff; --border: #e5e7eb; --text: #111827;
    --muted: #6b7280; --accent: #1d4ed8;
  }}
  body {{ font: 14px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif;
         background: var(--bg); color: var(--text); margin: 0; padding: 2em 1em; }}
  .page {{ max-width: 880px; margin: 0 auto; background: var(--panel);
           border: 1px solid var(--border); border-radius: 10px; padding: 2em 2.2em;
           box-shadow: 0 2px 18px rgba(0,0,0,0.04); }}
  header {{ border-bottom: 1px solid var(--border); padding-bottom: 1em; margin-bottom: 1.4em; }}
  h1 {{ margin: 0 0 0.2em; font-size: 1.6rem; letter-spacing: -0.01em; }}
  h2 {{ font-size: 1.1rem; margin: 1.6em 0 0.6em; color: var(--accent); }}
  .meta {{ color: var(--muted); font-size: 0.88em; }}
  table {{ width: 100%; border-collapse: collapse; margin: 0.6em 0 1.2em; }}
  th, td {{ padding: 0.45em 0.7em; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ font-size: 0.82em; color: var(--muted); text-transform: uppercase;
        letter-spacing: 0.04em; font-weight: 700; }}
  td {{ font-variant-numeric: tabular-nums; }}
  .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%;
          margin-right: 0.55em; vertical-align: middle; }}
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
           gap: 0.7em; margin: 1em 0 1.4em; }}
  .kpi {{ background: #f3f4f6; border-radius: 6px; padding: 0.7em 0.9em; }}
  .kpi .lbl {{ color: var(--muted); font-size: 0.78em; text-transform: uppercase;
                letter-spacing: 0.04em; font-weight: 700; }}
  .kpi .val {{ font-size: 1.35em; font-weight: 700; margin-top: 0.15em; }}
  figure {{ margin: 1em 0; }}
  figure img {{ width: 100%; height: auto; border-radius: 6px;
                border: 1px solid var(--border); }}
  figcaption {{ color: var(--muted); font-size: 0.84em; text-align: center;
                margin-top: 0.4em; }}
  ul {{ line-height: 1.7; padding-left: 1.2em; }}
  code {{ background: #f3f4f6; padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.9em; }}
  footer {{ margin-top: 2em; padding-top: 1em; border-top: 1px solid var(--border);
            color: var(--muted); font-size: 0.85em; text-align: center; }}
  @media print {{ body {{ padding: 0; background: #fff; }} .page {{ box-shadow: none; }} }}
</style>
</head><body>
<div class="page">

<header>
  <h1>ForecastingPath · Summary Report</h1>
  <div class="meta">Generated {s['generated_at']} · Git <code>{s['git_sha']}</code> · {s['n_events_resolved']} resolved events</div>
</header>

<div class="kpis">
  <div class="kpi"><div class="lbl">Live URL</div><div class="val" style="font-size:0.95em">agent.forecastingpath.com</div></div>
  <div class="kpi"><div class="lbl">Variant</div><div class="val" style="font-size:0.95em">multi_outcome_retrieval</div></div>
  <div class="kpi"><div class="lbl">Model</div><div class="val" style="font-size:0.95em">claude-opus-4-7</div></div>
  <div class="kpi"><div class="lbl">Brier (prod)</div><div class="val">{s['per_model']['Opus 4.7 (production)']['mean_brier']:.4f}</div></div>
  <div class="kpi"><div class="lbl">vs random</div><div class="val">{(1 - s['per_model']['Opus 4.7 (production)']['mean_brier'] / s['baselines']['random_binary']) * 100:.0f}%</div></div>
</div>

<h2>5-model endpoint Brier comparison (same pipeline, swap the LLM)</h2>
<table>
  <thead><tr><th>Model</th><th>Endpoint Brier</th><th>Binary p_yes</th><th>Full multi Brier</th><th>n endpoint</th></tr></thead>
  <tbody>
  {''.join(rows)}
  <tr><td><em>random 0.5 baseline</em></td><td>{s['baselines']['random_binary']:.4f}</td><td>—</td><td>—</td><td>—</td></tr>
  <tr><td><em>uniform 1/n prior</em></td><td>{s['baselines']['uniform_prior']:.4f}</td><td>—</td><td>—</td><td>—</td></tr>
  </tbody>
</table>
<p class="meta">Lower is better. Endpoint Brier uses <code>p_yes</code> against Prophet Arena's binary actuals for the 26-event sample-resolved set. Full multi Brier is shown only when the artifact contains per-outcome probabilities; blank means that run stored endpoint-style <code>p_yes</code> only.</p>

<h2>Where the win came from — floor fix vs model swap</h2>
<div class="results">
  <table>
    <thead><tr><th>Variant</th><th>Mean Brier</th><th>Δ from baseline</th></tr></thead>
    <tbody>
      <tr><td>Sonnet 4.6 + old floor (clamps binary to 0.25)</td><td class="brier">{phase1_sonnet_old_floor:.4f}</td><td>baseline</td></tr>
      <tr><td>Sonnet 4.6 + new floor (caps at 0.10)</td><td class="brier">{sonnet_new_floor:.4f}</td><td>−{delta_floor_only:.4f} ({pct_floor_share:.0f}% of the gap)</td></tr>
      <tr class="highlight"><td><strong>Opus 4.7 + new floor (production)</strong></td><td class="brier">{phase2_opus_new_floor:.4f}</td><td>−{delta_total:.4f} (full gap)</td></tr>
    </tbody>
  </table>
  <p class="note">The longshot-floor bug fix accounts for roughly <strong>85% of the Phase 2 improvement</strong>; the Sonnet→Opus swap accounts for the remaining ~15%. Numbers from Codex's paired-bootstrap branch (`scripts/bootstrap_brier_ci.py`), pinned-seed reproducible.</p>
</div>

<h2>Statistical significance — paired-bootstrap on the headline delta</h2>
<div class="results">
  <p>The Phase 2 improvement (0.0639 → 0.0379) on n=26 paired events:</p>
  <table>
    <tbody>
      <tr><td>Mean Brier improvement</td><td class="brier">{boot_mean:.4f}</td></tr>
      <tr><td>95% paired-bootstrap CI</td><td class="brier">[{boot_lo:.4f}, {boot_hi:.4f}]</td></tr>
      <tr><td>Resamples</td><td>{boot_n_resamples:,}</td></tr>
      <tr><td>Random seed</td><td>{boot_seed}</td></tr>
    </tbody>
  </table>
  <p class="note">CI excludes zero; the delta is significant at α=0.05 on this dataset. Standard caveat applies — n=26 is small and binary-skewed (16/26 sports matchups). A balanced-mix eval would likely widen the CI but not change the sign.</p>
</div>

<h2>Calibration curve · production (Opus 4.7) on binary events</h2>
<figure>
  <img src="summary_calibration.png" alt="Reliability diagram for the production model on 14 binary events">
  <figcaption>Bin-mean predicted vs bin-mean actual outcome. Diagonal = perfectly calibrated.</figcaption>
</figure>

<h2>Open-event ablations · 4-model agreement on 42 unresolved events</h2>
<table>
  <thead><tr><th>Dataset</th><th>n events</th><th>Mean p(outcome[0]) spread</th><th>Consensus (&lt;0.10)</th><th>Mid</th><th>Contested (&gt;0.30)</th></tr></thead>
  <tbody>
  {''.join(_open_rows(s))}
  </tbody>
</table>
<p class="meta">Models: Opus 4.7 (prod), Opus 4.6, Sonnet 4.6, GPT-5.2. Same pipeline; only the LLM call swaps. Spread = max(p_yes) − min(p_yes) across the 4 models per event. Consensus events likely have informative evidence; contested events flag where models genuinely disagree. Full per-event matrix on the auth-protected <code>/compare-open</code> page.</p>

<h2>Per-event Brier · production model</h2>
<figure>
  <img src="summary_per_event.png" alt="Per-event Brier loss for the production model, sorted">
  <figcaption>Each bar is one event. Wins (low Brier) on the left; losses (high Brier) on the right.</figcaption>
</figure>

<h2>Findings worth keeping</h2>
<ul>
  <li><strong>The win is JSON schema compliance, not raw reasoning.</strong>
    Three of four alternative LLMs in this table had Brier ≥ 0.22, dominated by
    catastrophic multi-outcome JSON failures (trailing commas, bogus outcome keys).
    Opus 4.7 followed our schema reliably; the alternatives didn't.</li>
  <li><strong>A silent production bug accounted for most of the Phase 2 win.</strong>
    Old longshot floor: <code>max(0.05, 0.5/n)</code> → 0.25 for binary events,
    clamping every binary prediction into [0.25, 0.75]. New: capped at the
    Kalshi-paper 0.10 threshold. ~6× per-event Brier improvement on binary
    longshots. Caught by smoke-testing against a synthetic Chiefs/SB-LXI event.</li>
  <li><strong>"Leaderboard #1 ≠ best in your pipeline."</strong>
    Gemini 3.1 Pro Preview is the public PA fixed-context leaderboard top.
    In our pipeline it placed last. Worth quoting any time someone proposes a
    model swap based on a public ranking.</li>
  <li><strong>Defensive engineering caught real bugs.</strong>
    JSON parse hardening (5 stages, trailing-comma repair, smart-quote normalization),
    fuzzy outcome-label matching, outcomes-missing safety net (binary heuristic
    + Haiku fallback). Verify gate now loud after silently swallowing pytest
    failures for an entire session.</li>
</ul>

<h2>Sample size honesty</h2>
<p class="meta">26 events is a small sample, skewed toward binary tennis matches.
The 0.0379 number is directionally validated, not converged. Live Prophet Arena
performance may differ by category mix; the multi-vendor evidence is the most
generalizable finding here. Full per-event detail at
<a href="/compare">/compare</a> (PIN required).</p>

<footer>
  Built by Rob Sneiderman · Team CanadaHacks · Project <em>The Oracles</em> · Prophet Hacks 2026.
  Source: <a href="https://github.com/Robby955/prophet-hacks">github.com/Robby955/prophet-hacks</a>.
</footer>

</div>
</body></html>
"""


# --- Matplotlib chart rendering ------------------------------------------


def _bin_predictions(probs: list[float], outcomes: list[int],
                     n_bins: int = 5) -> tuple[list[float], list[float], list[int]]:
    """Return (bin_centers, bin_mean_actual, bin_counts) for a reliability plot.

    Uses equal-width bins on [0, 1]. Empty bins are skipped.
    """
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(n_bins - 1, int(p * n_bins))
        bins[idx].append((p, o))
    centers, actuals, counts = [], [], []
    for i, items in enumerate(bins):
        if not items:
            continue
        ps = [p for p, _ in items]
        os = [o for _, o in items]
        centers.append(sum(ps) / len(ps))
        actuals.append(sum(os) / len(os))
        counts.append(len(items))
    return centers, actuals, counts


def _render_calibration_png(s: dict[str, Any], out: Path) -> None:
    prod = s["per_model"]["Opus 4.7 (production)"]
    fig, ax = plt.subplots(figsize=(6, 4), dpi=140)
    if prod["binary_p"]:
        centers, actuals, counts = _bin_predictions(
            prod["binary_p"], prod["binary_actual"], n_bins=5,
        )
        ax.plot([0, 1], [0, 1], color="#9ca3af", linestyle="--",
                linewidth=1, label="perfectly calibrated")
        ax.scatter(centers, actuals, s=[max(40, c * 25) for c in counts],
                   color=prod["color"], alpha=0.85, edgecolor="white",
                   linewidth=1.5, zorder=5)
        for c, a, n in zip(centers, actuals, counts):
            ax.annotate(f"n={n}", (c, a), textcoords="offset points",
                        xytext=(7, -2), fontsize=8, color="#374151")
    ax.set_xlim(-0.02, 1.02); ax.set_ylim(-0.02, 1.02)
    ax.set_xlabel("Predicted probability (bin mean)")
    ax.set_ylabel("Empirical outcome rate (bin mean)")
    ax.set_title("Reliability diagram · Opus 4.7 on binary events", fontsize=11)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", fontsize=8)
    plt.tight_layout()
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)


def _render_per_event_png(s: dict[str, Any], out: Path) -> None:
    prod = s["per_model"]["Opus 4.7 (production)"]
    all_briers = sorted(prod["binary_briers"])
    if not all_briers:
        return
    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=140)
    colors = [
        ("#86efac" if b < 0.05 else
         "#bef264" if b < 0.10 else
         "#fde68a" if b < 0.25 else
         "#fdba74" if b < 0.50 else
         "#fca5a5")
        for b in all_briers
    ]
    ax.bar(range(len(all_briers)), all_briers, color=colors,
           edgecolor="#374151", linewidth=0.3)
    ax.axhline(0.25, color="#9ca3af", linestyle="--", linewidth=1, label="random 0.5 baseline")
    ax.set_xlabel("Event index (sorted by Brier loss)")
    ax.set_ylabel("Brier loss (lower better)")
    ax.set_title(
        f"Per-event Brier · Opus 4.7 on {len(all_briers)} binary events "
        f"(mean {prod['binary_mean']:.4f})", fontsize=11,
    )
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(out, bbox_inches="tight")
    plt.close(fig)


def _render_pdf(s: dict[str, Any], pdf_path: Path) -> None:
    with PdfPages(pdf_path) as pdf:
        # Page 1: header + 5-model table + bar chart
        fig = plt.figure(figsize=(8.5, 11), dpi=130)
        fig.suptitle(
            f"ForecastingPath · Summary Report\n"
            f"{s['generated_at']}  ·  git {s['git_sha']}  ·  {s['n_events_resolved']} resolved events",
            fontsize=13, y=0.97,
        )

        labels = list(s["per_model"].keys())
        means = [s["per_model"][l]["mean_brier"] or 0 for l in labels]
        colors = [s["per_model"][l]["color"] for l in labels]
        ax1 = fig.add_axes([0.1, 0.55, 0.82, 0.30])
        bars = ax1.barh(labels[::-1], means[::-1], color=colors[::-1],
                        edgecolor="#1f2937", linewidth=0.3)
        for bar, m in zip(bars, means[::-1]):
            ax1.text(m + 0.005, bar.get_y() + bar.get_height() / 2,
                     f"{m:.4f}", va="center", fontsize=9)
        ax1.axvline(0.25, color="#9ca3af", linestyle="--", linewidth=1,
                    label="random 0.5 baseline")
        ax1.axvline(0.219, color="#9ca3af", linestyle=":", linewidth=1,
                    label="uniform 1/n prior")
        ax1.set_xlim(0, max(means) * 1.15)
        ax1.set_xlabel("Endpoint Brier (lower better)")
        ax1.set_title("5-model endpoint comparison: same pipeline, swap the LLM", fontsize=11)
        ax1.legend(loc="lower right", fontsize=8)
        ax1.grid(axis="x", alpha=0.25)

        # Reliability diagram in lower half
        prod = s["per_model"]["Opus 4.7 (production)"]
        ax2 = fig.add_axes([0.1, 0.08, 0.82, 0.35])
        if prod["binary_p"]:
            centers, actuals, counts = _bin_predictions(
                prod["binary_p"], prod["binary_actual"], n_bins=5,
            )
            ax2.plot([0, 1], [0, 1], color="#9ca3af", linestyle="--",
                     linewidth=1, label="perfectly calibrated")
            ax2.scatter(centers, actuals, s=[max(80, c * 50) for c in counts],
                        color=prod["color"], alpha=0.85, edgecolor="white",
                        linewidth=1.8, zorder=5)
            for c, a, n in zip(centers, actuals, counts):
                ax2.annotate(f"n={n}", (c, a),
                             textcoords="offset points", xytext=(8, -3),
                             fontsize=8, color="#1f2937")
        ax2.set_xlim(-0.02, 1.02); ax2.set_ylim(-0.02, 1.02)
        ax2.set_xlabel("Predicted probability (bin mean)")
        ax2.set_ylabel("Empirical outcome rate")
        ax2.set_title("Calibration · Opus 4.7 on binary events", fontsize=11)
        ax2.grid(True, alpha=0.25)
        ax2.legend(loc="upper left", fontsize=8)

        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Page 2: per-event Brier + findings
        fig2 = plt.figure(figsize=(8.5, 11), dpi=130)
        fig2.suptitle("Per-event Brier + Findings", fontsize=13, y=0.97)

        all_briers = sorted(prod["binary_briers"])
        if all_briers:
            ax3 = fig2.add_axes([0.1, 0.55, 0.82, 0.30])
            colors_bar = [
                ("#86efac" if b < 0.05 else
                 "#bef264" if b < 0.10 else
                 "#fde68a" if b < 0.25 else
                 "#fdba74" if b < 0.50 else
                 "#fca5a5")
                for b in all_briers
            ]
            ax3.bar(range(len(all_briers)), all_briers, color=colors_bar,
                    edgecolor="#374151", linewidth=0.3)
            ax3.axhline(0.25, color="#9ca3af", linestyle="--", linewidth=1)
            ax3.set_xlabel("Binary event index (sorted)")
            ax3.set_ylabel("Brier loss")
            ax3.set_title(f"{len(all_briers)} binary events  "
                          f"mean = {prod['binary_mean']:.4f}", fontsize=11)
            ax3.grid(axis="y", alpha=0.25)

        # Findings text panel
        findings = (
            "Findings worth keeping\n\n"
            "1. The Opus 4.7 win is JSON schema compliance, not raw reasoning.\n"
            "   Three of four alternative LLMs had Brier ≥ 0.22, dominated by\n"
            "   catastrophic multi-outcome JSON failures.\n\n"
            "2. Silent production bug accounted for most of the Phase 2 win.\n"
            "   Old longshot floor max(0.05, 0.5/n) returned 0.25 for binary,\n"
            "   silently clamping every binary prediction into [0.25, 0.75].\n"
            "   New formula caps at the Kalshi-paper 0.10 threshold.\n"
            "   ~6× per-event Brier improvement on binary longshots.\n\n"
            "3. Leaderboard #1 ≠ best in your pipeline.\n"
            "   Gemini 3.1 Pro Preview is public PA leaderboard's #1. In our\n"
            "   pipeline it placed last with multi-outcome Brier 0.81.\n\n"
            "4. Defensive engineering caught real bugs.\n"
            "   Parser hardening (5 stages), fuzzy outcome matching, outcomes-\n"
            "   missing safety net. Verify gate now loud after silently\n"
            "   swallowing pytest failures for an entire session.\n\n"
            "Honest caveats\n\n"
            "• 26 events is a small sample, skewed toward binary tennis matches.\n"
            "• Live Prophet Arena performance may differ by category mix.\n"
            "• Multi-vendor evidence is the most generalizable finding here.\n"
        )
        ax_text = fig2.add_axes([0.1, 0.05, 0.82, 0.42])
        ax_text.text(0, 1, findings, family="monospace", fontsize=8.5,
                     verticalalignment="top", color="#111827")
        ax_text.axis("off")

        pdf.savefig(fig2, bbox_inches="tight")
        plt.close(fig2)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="static/", help="output directory")
    args = p.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"computing summary from data/predictions/ ...")
    s = _compute_summary()

    # Charts
    cal_png = out_dir / "summary_calibration.png"
    per_event_png = out_dir / "summary_per_event.png"
    print(f"rendering {cal_png}")
    _render_calibration_png(s, cal_png)
    print(f"rendering {per_event_png}")
    _render_per_event_png(s, per_event_png)

    # HTML
    html_path = out_dir / "summary.html"
    print(f"writing {html_path}")
    html_path.write_text(_render_html(s))

    # PDF
    pdf_path = out_dir / "summary.pdf"
    print(f"writing {pdf_path}")
    _render_pdf(s, pdf_path)

    print(f"\ndone. open {html_path} or {pdf_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
