#!/usr/bin/env python3
"""Composite scorer for forecast variants.

Per the locked strategic decisions (memory: project_locked_strategic_decisions):
optimizing single-metric Brier overfits to one number. The right comparison is
a composite that penalizes overconfidence, longshot violations, cost, and
fragile (uniform-fallback) sources.

    score = brier
          + lambda_oc   * overconfidence_penalty
          + lambda_ls   * longshot_violation_rate
          + lambda_cost * cost_per_event_normalized
          + lambda_fs   * fragile_source_rate

All components are >= 0 and lower-is-better, so the composite is also
lower-is-better. Lambdas are tunable; the defaults below weight Brier most
heavily but make the other terms visible enough to break ties.

Usage:
    python scripts/composite_score.py \
        --events data/resolved.json \
        --predictions-dir data/predictions \
        --out reports/composite_summary.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# Cost-per-event in USD, hand-curated from the variant docstrings.
# Used to compute cost_per_event_normalized = cost / max_cost_across_variants.
COST_PER_EVENT_USD: dict[str, float] = {
    "uniform_prior": 0.0,
    "single_llm": 0.005,
    "opus_47": 0.015,
    "opus_46": 0.015,
    "gpt55": 0.010,
    "gpt52": 0.010,
    "ensemble_logit": 0.015,
    "ensemble_leaderboard": 0.025,
    "sonnet_cot": 0.007,
    "sonnet_cot_shrink": 0.007,
    "multi_outcome": 0.010,
    "multi_outcome_sc3": 0.030,
    "multi_outcome_retrieval": 0.012,
    "hybrid_routed": 0.008,
}


def longshot_floor(n_outcomes: int) -> float:
    """Match forecast_track.longshot_guard_floor."""
    if n_outcomes <= 0:
        return 0.05
    return max(0.05, 0.5 / n_outcomes)


def distribute_p_yes(p_yes: float, outcomes: list[str]) -> dict[str, float]:
    """Match the server-side / agent-side legacy translation for binary variants."""
    n = len(outcomes)
    if n == 0:
        return {}
    if n == 1:
        return {outcomes[0]: p_yes}
    rest = max(0.0, (1.0 - p_yes) / (n - 1))
    return {o: (p_yes if i == 0 else rest) for i, o in enumerate(outcomes)}


def per_event_brier(probs: dict[str, float], outcomes: list[str], winner: str) -> float:
    s = 0.0
    for o in outcomes:
        p = probs.get(o, 0.0)
        y = 1.0 if o == winner else 0.0
        s += (p - y) ** 2
    return s


def overconfidence_penalty(probs: dict[str, float], outcomes: list[str], winner: str) -> float:
    """Penalize predicting > 0.7 on a wrong outcome OR < 0.3 on the right one.

    Returns a value in [0, 1] per event. Squared in the composite to weight
    very-wrong-confident predictions much more than slightly-wrong ones.
    """
    pen = 0.0
    for o in outcomes:
        p = probs.get(o, 0.0)
        if o == winner and p < 0.3:
            pen = max(pen, 0.3 - p)
        elif o != winner and p > 0.7:
            pen = max(pen, p - 0.7)
    return pen


def longshot_violations(probs: dict[str, float], outcomes: list[str]) -> int:
    """Count outcomes whose probability is below the Kalshi floor."""
    floor = longshot_floor(len(outcomes))
    return sum(1 for o in outcomes if probs.get(o, 0.0) < floor - 1e-6)


def fragile_source_event(prediction: dict) -> bool:
    """Detect a fallback-to-uniform-prior event from the rationale text."""
    r = (prediction.get("rationale") or "").lower()
    return "uniform prior over" in r and "no information" in r or r.startswith(
        "uniform prior"
    )


def score_variant(
    *, variant: str,
    predictions: list[dict],
    events: list[dict],
    lambda_oc: float = 0.5,
    lambda_ls: float = 0.1,
    lambda_cost: float = 0.05,
    lambda_fs: float = 0.2,
    max_cost: float = 0.030,
) -> dict[str, float]:
    """Return composite + components for one variant on one event set."""
    # Index events by market_ticker
    ev_by_t = {(e.get("market_ticker") or e.get("event_ticker")): e for e in events}

    n_scored = 0
    brier_sum = 0.0
    oc_sq_sum = 0.0
    ls_violation_count = 0
    fragile_count = 0
    total_outcomes = 0

    for p in predictions:
        t = p.get("market_ticker")
        ev = ev_by_t.get(t)
        if ev is None:
            continue
        outcomes = ev.get("outcomes") or []
        res = (ev.get("resolved_outcome") or {}).get("value") or []
        if not outcomes or not res:
            continue
        winner = res[0] if isinstance(res, list) else res

        # Recover the per-outcome distribution from the prediction payload.
        # For multi-outcome variants the prediction file from backtest writes
        # only `p_yes` (legacy backtest output). We treat that as outcomes[0]
        # and distribute. Variants that ARE multi-outcome internally still
        # show up with the right p_yes for outcomes[0] in the legacy file.
        p_yes = float(p.get("p_yes", 1.0 / max(1, len(outcomes))))
        probs = distribute_p_yes(p_yes, outcomes)

        brier_sum += per_event_brier(probs, outcomes, winner)
        oc_pen = overconfidence_penalty(probs, outcomes, winner)
        oc_sq_sum += oc_pen * oc_pen
        ls_violation_count += longshot_violations(probs, outcomes)
        total_outcomes += len(outcomes)
        if fragile_source_event(p):
            fragile_count += 1
        n_scored += 1

    if n_scored == 0:
        return {"n_scored": 0}

    brier = brier_sum / n_scored
    oc_mean_sq = oc_sq_sum / n_scored
    ls_rate = ls_violation_count / max(1, total_outcomes)
    fragile_rate = fragile_count / n_scored
    cost = COST_PER_EVENT_USD.get(variant, 0.010)
    cost_norm = cost / max_cost if max_cost > 0 else 0.0

    composite = (
        brier
        + lambda_oc * oc_mean_sq
        + lambda_ls * ls_rate
        + lambda_cost * cost_norm
        + lambda_fs * fragile_rate
    )
    return {
        "n_scored": n_scored,
        "brier": round(brier, 4),
        "overconfidence_sq_mean": round(oc_mean_sq, 4),
        "longshot_violation_rate": round(ls_rate, 4),
        "cost_per_event_usd": round(cost, 4),
        "cost_norm": round(cost_norm, 4),
        "fragile_source_rate": round(fragile_rate, 4),
        "composite": round(composite, 4),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="data/resolved.json")
    ap.add_argument("--predictions-dir", default="data/predictions")
    ap.add_argument("--out", default="reports/composite_summary.md")
    ap.add_argument("--lambda-oc", type=float, default=0.5)
    ap.add_argument("--lambda-ls", type=float, default=0.1)
    ap.add_argument("--lambda-cost", type=float, default=0.05)
    ap.add_argument("--lambda-fs", type=float, default=0.2)
    args = ap.parse_args(argv)

    events = json.loads(Path(args.events).read_text())
    pred_dir = Path(args.predictions_dir)
    max_cost = max(COST_PER_EVENT_USD.values())

    rows: list[dict[str, Any]] = []
    for pf in sorted(pred_dir.glob("*.json")):
        if pf.name in ("backtest_summary.json",):
            continue
        variant = pf.stem
        try:
            data = json.loads(pf.read_text())
            preds = data.get("predictions") or []
        except Exception:
            continue
        if not preds:
            continue
        score = score_variant(
            variant=variant,
            predictions=preds,
            events=events,
            lambda_oc=args.lambda_oc,
            lambda_ls=args.lambda_ls,
            lambda_cost=args.lambda_cost,
            lambda_fs=args.lambda_fs,
            max_cost=max_cost,
        )
        if score.get("n_scored", 0) > 0:
            score["variant"] = variant
            rows.append(score)

    rows.sort(key=lambda r: r.get("composite", float("inf")))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Composite variant comparison",
        "",
        f"Lambdas: oc={args.lambda_oc}, ls={args.lambda_ls}, "
        f"cost={args.lambda_cost}, fs={args.lambda_fs}. Lower composite = better.",
        "",
        "| variant | n | brier | overconf² | longshot% | cost/ev | fragile% | composite |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['variant']} | {r['n_scored']} | {r['brier']:.4f} | "
            f"{r['overconfidence_sq_mean']:.4f} | "
            f"{r['longshot_violation_rate']*100:.1f}% | "
            f"${r['cost_per_event_usd']:.4f} | "
            f"{r['fragile_source_rate']*100:.1f}% | "
            f"**{r['composite']:.4f}** |"
        )
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
