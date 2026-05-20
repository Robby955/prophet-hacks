"""Consolidated forecast diagnostics — the measurement engine.

Takes one prediction file + the resolved event set and produces a single
stratified diagnostics view: overall Brier, Murphy decomposition
(reliability / resolution / uncertainty), a reliability diagram with ECE, and
Brier broken out by category, outcome-count, and confidence bucket.

The point is to surface the *largest systematic residual* — the only kind of
error we have the statistical power to act on at small n — rather than chase
per-event noise. Feed it the leakage-free predictions
(`ablation_search_brave_fresh.json`) for honest numbers; it ingests any
standard prediction file as the resolved set grows (shadow + live).

Reuses evaluation/brier.py so the numbers match every other surface.

Usage:
    .venv/bin/python scripts/diagnostics.py \
        --pred data/predictions/ablation_search_brave_fresh.json \
        --label brave_fresh --html-out static/diagnostics.html
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.brier import brier_score, murphy_decomposition  # noqa: E402
from evaluation.validation import probability_bin_index  # noqa: E402

CONF_EDGES = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0001]


def _load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def _pred_rows(payload: Any) -> list[dict]:
    rows = payload.get("predictions", payload) if isinstance(payload, dict) else payload
    return [r for r in rows if isinstance(r, dict)]


def _winner(ev: dict) -> str | None:
    ro = ev.get("resolved_outcome") or {}
    val = ro.get("value") if isinstance(ro, dict) else None
    return val[0] if isinstance(val, list) and val else None


def _prob_vector(row: dict, outcomes: list[str]) -> list[float]:
    """Per-outcome probabilities, defensively reconstructed."""
    probs = row.get("probabilities")
    if isinstance(probs, list) and probs and isinstance(probs[0], dict):
        by_market = {str(p.get("market")): float(p.get("probability", 0.0)) for p in probs}
        return [by_market.get(o, 0.0) for o in outcomes]
    # binary-style fallback: outcomes[0] = p_yes, rest split the remainder
    p_yes = float(row.get("p_yes", 1.0 / max(len(outcomes), 1)))
    if len(outcomes) <= 1:
        return [p_yes]
    rest = (1.0 - p_yes) / (len(outcomes) - 1)
    return [p_yes] + [rest] * (len(outcomes) - 1)


def build_records(pred_path: str, resolved_path: str) -> list[dict]:
    preds = {r.get("market_ticker") or r.get("event_ticker"): r for r in _pred_rows(_load(pred_path))}
    events = _load(resolved_path)
    if isinstance(events, dict):
        events = list(events.values())

    records: list[dict] = []
    for ev in events:
        tk = ev.get("market_ticker")
        if tk not in preds:
            continue
        outcomes = ev.get("outcomes") or []
        winner = _winner(ev)
        if not outcomes or winner not in outcomes:
            continue
        row = preds[tk]
        vec = _prob_vector(row, outcomes)
        wi = outcomes.index(winner)

        # binary view on outcome[0] (matches PA CLI single-binary scoring)
        p_yes = vec[0] if vec else 0.5
        actual0 = 1 if wi == 0 else 0
        binary_brier = brier_score(min(max(p_yes, 0.0), 1.0), actual0)

        # proper multiclass Brier
        multi_brier = sum((p - (1.0 if i == wi else 0.0)) ** 2 for i, p in enumerate(vec))

        records.append({
            "ticker": tk,
            "category": ev.get("category") or "Uncategorized",
            "n_outcomes": len(outcomes),
            "kind": "binary" if len(outcomes) == 2 else "multi",
            "p_yes": p_yes,
            "actual0": actual0,
            "winner_prob": vec[wi] if wi < len(vec) else 0.0,
            "confidence": max(vec) if vec else 0.0,
            "binary_brier": binary_brier,
            "multi_brier": multi_brier,
        })
    return records


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def reliability(records: list[dict], n_bins: int = 10) -> dict:
    """Reliability diagram on the binary (outcome[0]) view + ECE."""
    bins = defaultdict(lambda: {"preds": [], "actuals": []})
    for r in records:
        idx = probability_bin_index(min(max(r["p_yes"], 0.0), 0.999999), n_bins)
        bins[idx]["preds"].append(r["p_yes"])
        bins[idx]["actuals"].append(r["actual0"])
    n = len(records)
    rows, ece = [], 0.0
    for idx in range(n_bins):
        b = bins.get(idx)
        if not b or not b["preds"]:
            rows.append({"bin": idx, "lo": idx / n_bins, "hi": (idx + 1) / n_bins,
                         "count": 0, "mean_pred": None, "emp_freq": None})
            continue
        mp = _mean(b["preds"])
        ef = _mean([float(a) for a in b["actuals"]])
        cnt = len(b["preds"])
        ece += (cnt / n) * abs(mp - ef)
        rows.append({"bin": idx, "lo": idx / n_bins, "hi": (idx + 1) / n_bins,
                     "count": cnt, "mean_pred": mp, "emp_freq": ef})
    return {"bins": rows, "ece": ece, "n": n}


def stratify(records: list[dict], key: str) -> list[dict]:
    groups: dict[Any, list[dict]] = defaultdict(list)
    for r in records:
        groups[r[key]].append(r)
    out = []
    for g, rs in groups.items():
        out.append({
            "group": str(g),
            "n": len(rs),
            "binary_brier": _mean([r["binary_brier"] for r in rs]),
            "multi_brier": _mean([r["multi_brier"] for r in rs]),
            "mean_conf": _mean([r["confidence"] for r in rs]),
            "mean_winner_prob": _mean([r["winner_prob"] for r in rs]),
        })
    return sorted(out, key=lambda x: (-(x["binary_brier"] or 0), -x["n"]))


def conf_buckets(records: list[dict]) -> list[dict]:
    out = []
    for i in range(len(CONF_EDGES) - 1):
        lo, hi = CONF_EDGES[i], CONF_EDGES[i + 1]
        rs = [r for r in records if lo <= r["confidence"] < hi]
        out.append({
            "group": f"{lo:.1f}-{min(hi,1.0):.1f}",
            "n": len(rs),
            "binary_brier": _mean([r["binary_brier"] for r in rs]),
            "mean_winner_prob": _mean([r["winner_prob"] for r in rs]),
        })
    return out


def analyze(records: list[dict], label: str) -> dict:
    binary = [r for r in records if r["kind"] == "binary"]
    murphy = murphy_decomposition([r["p_yes"] for r in binary],
                                  [r["actual0"] for r in binary]) if binary else None
    return {
        "label": label,
        "n_events": len(records),
        "n_binary": len(binary),
        "n_multi": len(records) - len(binary),
        "mean_binary_brier": _mean([r["binary_brier"] for r in records]),
        "mean_multi_brier": _mean([r["multi_brier"] for r in records]),
        "murphy": {"reliability": murphy.reliability, "resolution": murphy.resolution,
                   "uncertainty": murphy.uncertainty, "brier": murphy.brier} if murphy else None,
        "reliability": reliability(records),
        "by_category": stratify(records, "category"),
        "by_kind": stratify(records, "kind"),
        "by_confidence": conf_buckets(records),
    }


def find_largest_residual(report: dict) -> str:
    """One-line headline: where is the biggest systematic miss?"""
    notes = []
    rel = report["reliability"]
    if rel["ece"] is not None:
        notes.append(f"ECE={rel['ece']:.3f}")
    # over/under-confidence direction from populated bins
    diffs = [(b["mean_pred"] - b["emp_freq"], b["count"]) for b in rel["bins"]
             if b["count"] and b["mean_pred"] is not None]
    if diffs:
        signed = sum(d * c for d, c in diffs) / sum(c for _, c in diffs)
        direction = "OVER-confident (predicts higher than reality)" if signed > 0.02 else \
                    "UNDER-confident (predicts lower than reality)" if signed < -0.02 else \
                    "roughly calibrated"
        notes.append(f"mean(pred−actual)={signed:+.3f} → {direction}")
    cats = [c for c in report["by_category"] if c["n"] >= 3 and c["binary_brier"] is not None]
    if cats:
        worst = cats[0]
        notes.append(f"worst category: {worst['group']} (binary Brier {worst['binary_brier']:.3f}, n={worst['n']})")
    return " | ".join(notes)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def render_html(report: dict) -> str:
    rel = report["reliability"]
    W, H, pad = 360, 360, 44
    def x(p): return pad + p * (W - 2 * pad)
    def y(p): return H - pad - p * (H - 2 * pad)
    pts = [(b["mean_pred"], b["emp_freq"], b["count"]) for b in rel["bins"]
           if b["count"] and b["mean_pred"] is not None]
    dots = "".join(
        f'<circle cx="{x(mp):.1f}" cy="{y(ef):.1f}" r="{4+min(cnt,8):.0f}" '
        f'fill="#1a4d8f" fill-opacity="0.35" stroke="#1a4d8f"/>' for mp, ef, cnt in pts)
    line = "".join(f'{"M" if i==0 else "L"}{x(mp):.1f},{y(ef):.1f}'
                   for i, (mp, ef, _) in enumerate(pts))
    grid = "".join(f'<line x1="{x(t):.0f}" y1="{y(0):.0f}" x2="{x(t):.0f}" y2="{y(1):.0f}" stroke="#eee"/>'
                   f'<line x1="{x(0):.0f}" y1="{y(t):.0f}" x2="{x(1):.0f}" y2="{y(t):.0f}" stroke="#eee"/>'
                   for t in [0, .25, .5, .75, 1])

    def table(rows, cols):
        head = "".join(f"<th>{c[1]}</th>" for c in cols)
        body = ""
        for r in rows:
            tds = ""
            for key, _ in cols:
                v = r.get(key)
                if isinstance(v, float):
                    tds += f"<td class=mono>{v:.3f}</td>"
                else:
                    tds += f"<td>{'' if v is None else v}</td>"
            body += f"<tr>{tds}</tr>"
        return f"<table><tr>{head}</tr>{body}</table>"

    m = report["murphy"]
    murphy_html = (f"reliability <b>{m['reliability']:.4f}</b> (lower=better) · "
                   f"resolution <b>{m['resolution']:.4f}</b> (higher=better) · "
                   f"uncertainty {m['uncertainty']:.4f}") if m else "n/a"

    return f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Diagnostics — {report['label']}</title>
<style>
body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#1b1f24;background:#f7f8fa;margin:0}}
.wrap{{max-width:980px;margin:0 auto;padding:26px 20px 70px}}
h1{{font-size:22px;margin:0 0 2px}} .sub{{color:#5b6470;font-size:13px;margin:0 0 16px}}
h2{{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:#5b6470;margin:26px 0 10px;border-bottom:1px solid #e3e7ec;padding-bottom:6px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap}} .card{{background:#fff;border:1px solid #e3e7ec;border-radius:10px;padding:12px 16px;min-width:150px}}
.big{{font-size:26px;font-weight:700;font-family:ui-monospace,Menlo,monospace}}
.muted{{color:#5b6470;font-size:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#fff;border:1px solid #e3e7ec;border-radius:8px;overflow:hidden}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #eef1f4}} th{{font-size:11px;text-transform:uppercase;color:#5b6470}}
.mono{{font-family:ui-monospace,Menlo,monospace;font-size:12px}}
.flex{{display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start}}
.banner{{border-left:4px solid #9a6700;background:#fff8e8;border-radius:8px;padding:11px 15px;font-size:13.5px;margin:0 0 6px}}
svg text{{font-size:10px;fill:#5b6470}}
</style></head><body><div class=wrap>
<h1>Forecast diagnostics — {report['label']}</h1>
<p class=sub>{report['n_events']} resolved events ({report['n_binary']} binary, {report['n_multi']} multi) · hand-run snapshot · numbers from evaluation/brier.py</p>

<div class=banner><b>Largest systematic residual:</b> {find_largest_residual(report)}</div>

<div class=cards>
  <div class=card><div class=muted>binary Brier</div><div class=big>{report['mean_binary_brier']:.3f}</div></div>
  <div class=card><div class=muted>multiclass Brier</div><div class=big>{report['mean_multi_brier']:.3f}</div></div>
  <div class=card><div class=muted>ECE (calibration error)</div><div class=big>{rel['ece']:.3f}</div></div>
</div>

<h2>Reliability diagram <span style="text-transform:none;color:#5b6470;font-weight:400">— dot on the line = calibrated; above = under-confident; below = over-confident</span></h2>
<div class=flex>
<svg width="{W}" height="{H}" style="background:#fff;border:1px solid #e3e7ec;border-radius:8px">
  {grid}
  <line x1="{x(0):.0f}" y1="{y(0):.0f}" x2="{x(1):.0f}" y2="{y(1):.0f}" stroke="#b3261e" stroke-dasharray="4 3"/>
  <path d="{line}" fill=none stroke="#1a4d8f" stroke-width=1.5/>
  {dots}
  <text x="{x(0.5):.0f}" y="{H-14}" text-anchor=middle>predicted probability →</text>
  <text x="14" y="{y(0.5):.0f}" text-anchor=middle transform="rotate(-90 14 {y(0.5):.0f})">empirical frequency →</text>
</svg>
<div style="flex:1;min-width:280px">
<p class=muted>Murphy decomposition (binary): {murphy_html}</p>
{table(report['by_confidence'], [("group","confidence"),("n","n"),("binary_brier","Brier"),("mean_winner_prob","winner p")])}
</div>
</div>

<h2>By category</h2>
{table(report['by_category'], [("group","category"),("n","n"),("binary_brier","binary Brier"),("multi_brier","multi Brier"),("mean_conf","mean conf")])}

<h2>Binary vs multi</h2>
{table(report['by_kind'], [("group","kind"),("n","n"),("binary_brier","binary Brier"),("multi_brier","multi Brier"),("mean_winner_prob","winner p")])}

</div></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", required=True, help="prediction file (standard shape)")
    ap.add_argument("--resolved", default="data/resolved.json")
    ap.add_argument("--label", default="")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--html-out", default="")
    args = ap.parse_args()

    label = args.label or Path(args.pred).stem
    records = build_records(args.pred, args.resolved)
    if not records:
        print("no matched resolved predictions — check --pred / --resolved", file=sys.stderr)
        return 1
    report = analyze(records, label)

    # console
    print(f"=== diagnostics: {label} ===")
    print(f"events {report['n_events']} (binary {report['n_binary']}, multi {report['n_multi']})")
    print(f"binary Brier {report['mean_binary_brier']:.4f} | multiclass Brier {report['mean_multi_brier']:.4f} "
          f"| ECE {report['reliability']['ece']:.4f}")
    if report["murphy"]:
        m = report["murphy"]
        print(f"Murphy: reliability {m['reliability']:.4f} (low=good), resolution {m['resolution']:.4f} (high=good), "
              f"uncertainty {m['uncertainty']:.4f}")
    print(f">>> {find_largest_residual(report)}")
    print("\nby category (worst first):")
    for c in report["by_category"]:
        bb = f"{c['binary_brier']:.4f}" if c["binary_brier"] is not None else "  -  "
        print(f"  {c['group']:<16} n={c['n']:<3} binary {bb}")
    print("\nby confidence bucket:")
    for c in report["by_confidence"]:
        bb = f"{c['binary_brier']:.4f}" if c["binary_brier"] is not None else "  -  "
        wp = f"{c['mean_winner_prob']:.3f}" if c.get("mean_winner_prob") is not None else "  -  "
        print(f"  {c['group']:<10} n={c['n']:<3} binary {bb}  winner_p {wp}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2))
        print(f"\nwrote {args.json_out}")
    if args.html_out:
        Path(args.html_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.html_out).write_text(render_html(report))
        print(f"wrote {args.html_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
