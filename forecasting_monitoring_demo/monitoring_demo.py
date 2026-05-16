#!/usr/bin/env python3
"""Tiny Prophet-style monitoring demo.

This is not a trading bot. It is a toy offline harness that shows the monitoring
shape we want before live events exist:

- market prior
- model forecasts
- credibility-weighted logit blend
- Kalshi-inspired longshot guard
- executable edge checks
- JSONL traces
- Brier/ECE/funnel summaries

Run:
    python monitoring_demo.py --events toy_events.jsonl --out outputs
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

EDGE_THRESHOLD = 0.08
P_MIN = 0.01
P_MAX = 0.99


def clamp(p: float, lo: float = P_MIN, hi: float = P_MAX) -> float:
    return min(max(float(p), lo), hi)


def logit(p: float) -> float:
    p = clamp(p)
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def brier(p: float, y: float) -> float:
    return (clamp(p) - float(y)) ** 2


def ece(records: list[dict[str, Any]], prob_key: str, bins: int = 5) -> float:
    """Simple empirical calibration error over equal-width bins."""
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        p = clamp(r[prob_key])
        idx = min(int(p * bins), bins - 1)
        groups[idx].append(r)
    n = len(records) or 1
    err = 0.0
    for group in groups.values():
        p_hat = sum(clamp(r[prob_key]) for r in group) / len(group)
        y_hat = sum(float(r["outcome"]) for r in group) / len(group)
        err += len(group) / n * abs(y_hat - p_hat)
    return err


def median(xs: list[float]) -> float:
    xs = sorted(xs)
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else 0.5 * (xs[m - 1] + xs[m])


def model_agreement(probs: list[float]) -> float:
    """Map forecast dispersion to [0,1], where 1 means high agreement."""
    if len(probs) <= 1:
        return 1.0
    sd = statistics.pstdev(probs)
    return max(0.0, 1.0 - sd / 0.20)


def blended_forecast(row: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Credibility-weighted logit blend, with a longshot guard.

    Longshot guard: the Kalshi paper finds low-price contracts are often
    overestimated by traders. When the market is below 10c, we require much
    stronger evidence before allowing the model to pull the forecast far away
    from the market prior.
    """
    p_market = clamp(row["p_market"])
    probs = [clamp(row["gpt55_p"]), clamp(row["opus_p"])]
    p_model = median(probs)
    disagreement = statistics.pstdev(probs)
    agreement = model_agreement(probs)
    source_quality = max(0.0, min(1.0, float(row["source_quality"])))
    horizon_weight = max(0.0, min(1.0, float(row["horizon_weight"])))

    credibility = 0.10 + 0.45 * source_quality + 0.25 * agreement + 0.20 * horizon_weight
    credibility = max(0.05, min(0.75, credibility))

    longshot_guard = False
    if p_market < 0.10 and p_model > p_market + 0.06:
        credibility *= 0.30
        longshot_guard = True

    if p_market > 0.90 and p_model < p_market - 0.06:
        # Favorite guard: do not let model conservatism erase a strong market
        # signal unless source quality is excellent.
        credibility *= 0.50 if source_quality < 0.85 else 0.80

    p_final = sigmoid(logit(p_market) + credibility * (logit(p_model) - logit(p_market)))

    meta = {
        "p_model_median": p_model,
        "model_disagreement": disagreement,
        "model_agreement": agreement,
        "credibility": credibility,
        "longshot_guard": longshot_guard,
    }
    return clamp(p_final), meta


def decide(row: dict[str, Any], p_final: float) -> dict[str, Any]:
    yes_edge = p_final - float(row["yes_ask"])
    no_ask = 1.0 - float(row["yes_bid"])
    no_edge = (1.0 - p_final) - no_ask

    if yes_edge >= EDGE_THRESHOLD and yes_edge >= no_edge:
        return {"action": "BUY", "side": "YES", "edge": yes_edge, "skip_reason": None}
    if no_edge >= EDGE_THRESHOLD:
        return {"action": "BUY", "side": "NO", "edge": no_edge, "skip_reason": None}

    return {
        "action": "SKIP",
        "side": None,
        "edge": max(yes_edge, no_edge),
        "skip_reason": f"edge below threshold; yes_edge={yes_edge:.3f}, no_edge={no_edge:.3f}",
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def make_charts(rows: list[dict[str, Any]], out_dir: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return

    variants = ["p_market", "gpt55_p", "opus_p", "p_final"]
    labels = ["Market", "GPT-5.5", "Opus", "Final blend"]
    vals = [sum(brier(r[v], r["outcome"]) for r in rows) / len(rows) for v in variants]

    fig = plt.figure(figsize=(7, 4.2))
    ax = fig.add_subplot(111)
    ax.bar(labels, vals)
    ax.set_title("Toy Brier score by forecast source")
    ax.set_ylabel("Brier score lower is better")
    ax.set_ylim(0, max(vals) * 1.25)
    for i, v in enumerate(vals):
        ax.text(i, v + max(vals) * 0.03, f"{v:.3f}", ha="center")
    fig.tight_layout()
    fig.savefig(out_dir / "brier_by_variant.png", dpi=160)
    plt.close(fig)

    funnel = [
        ("seen", len(rows)),
        ("forecasted", len(rows)),
        ("retrieval_used", sum(1 for r in rows if r["source_quality"] >= 0.60)),
        ("trade_candidates", sum(1 for r in rows if r["action"] == "BUY")),
        ("skipped", sum(1 for r in rows if r["action"] == "SKIP")),
    ]
    fig = plt.figure(figsize=(7, 4.2))
    ax = fig.add_subplot(111)
    ax.bar([x[0] for x in funnel], [x[1] for x in funnel])
    ax.set_title("Toy decision funnel")
    ax.set_ylabel("Count")
    ax.set_ylim(0, max(x[1] for x in funnel) + 1)
    for i, (_, v) in enumerate(funnel):
        ax.text(i, v + 0.1, str(v), ha="center")
    fig.tight_layout()
    fig.savefig(out_dir / "decision_funnel.png", dpi=160)
    plt.close(fig)


def make_summary(rows: list[dict[str, Any]]) -> str:
    variants = ["p_market", "gpt55_p", "opus_p", "p_final"]
    names = {"p_market": "Market", "gpt55_p": "GPT-5.5", "opus_p": "Opus", "p_final": "Final blend"}
    lines = []
    lines.append("# Monitoring Demo Summary")
    lines.append("")
    lines.append("## Decision funnel")
    lines.append("")
    c = Counter(r["action"] for r in rows)
    lines.append(f"- Markets seen: {len(rows)}")
    lines.append(f"- Forecasted: {len(rows)}")
    lines.append(f"- Retrieval/high-source-quality cases: {sum(1 for r in rows if r['source_quality'] >= 0.60)}")
    lines.append(f"- BUY decisions: {c.get('BUY', 0)}")
    lines.append(f"- SKIP decisions: {c.get('SKIP', 0)}")
    lines.append("")
    lines.append("## Forecast metrics")
    lines.append("")
    lines.append("| Variant | Mean Brier | ECE |")
    lines.append("|---|---:|---:|")
    for v in variants:
        mean_brier = sum(brier(r[v], r["outcome"]) for r in rows) / len(rows)
        lines.append(f"| {names[v]} | {mean_brier:.4f} | {ece(rows, v):.4f} |")
    lines.append("")
    lines.append("## Decisions")
    lines.append("")
    lines.append("| Market | Domain | p_market | p_final | Action | Side | Edge | Note |")
    lines.append("|---|---|---:|---:|---|---|---:|---|")
    for r in rows:
        lines.append(
            f"| {r['market_id']} | {r['domain']} | {r['p_market']:.2f} | {r['p_final']:.2f} | "
            f"{r['action']} | {r.get('side') or ''} | {r['edge']:.3f} | {r.get('skip_reason') or 'edge cleared'} |"
        )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("The demo is intentionally tiny, so the metrics are not statistically meaningful. The goal is operational: confirm that every market produces a trace, every skip has a reason, and the final forecast can be compared against market-only and model-only baselines.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default="toy_events.jsonl")
    parser.add_argument("--out", default="outputs")
    args = parser.parse_args()

    event_path = Path(args.events)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for row in load_jsonl(event_path):
        p_final, meta = blended_forecast(row)
        decision = decide(row, p_final)
        trace = {
            **row,
            **meta,
            **decision,
            "p_final": p_final,
            "yes_edge": p_final - float(row["yes_ask"]),
            "no_edge": (1.0 - p_final) - (1.0 - float(row["yes_bid"])),
            "cost_estimate_usd": 0.00,
            "trace_version": "toy-monitor-v1",
        }
        rows.append(trace)

    write_jsonl(out_dir / "demo_trace.jsonl", rows)
    (out_dir / "monitor_summary.md").write_text(make_summary(rows), encoding="utf-8")
    make_charts(rows, out_dir)

    print((out_dir / "monitor_summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
