"""Confidence-gated abstain sweep.

Diagnostics (reports/diagnostics_brave_fresh.json) show the forecaster's
skill is concentrated in CONFIDENT calls: at confidence >= 0.8 the binary
Brier is ~0.02 (excellent), but in the uncertain 0.5-0.7 band it climbs to
~0.26 (worse than a coin flip). That motivates a confidence-gating /
abstain policy: when the model is not confident, fall back to a no-skill
default instead of trusting the forecast.

This script quantifies that. For each leakage-free prediction we take the
confidence = max(per-outcome probability). For a sweep of thresholds tau we
REPLACE every low-confidence (confidence < tau) forecast with the no-skill
uniform fallback (binary: p_yes -> 0.5; multiclass: 1/n on every outcome)
and keep the rest. We then measure mean binary Brier (outcome[0] view) and
mean multiclass Brier across all events at every tau, find the tau* that
minimises binary Brier, and report the gated Brier there versus the
ungated baseline.

IMPORTANT CAVEAT: the real-world abstain fallback is the MARKET PRICE,
which is unavailable for this offline resolved set. Uniform-0.5 is only a
proxy / lower bound on the benefit of gating -- a market-price fallback
would almost certainly do better than uniform. Treat the reported gains as
conservative.

Conventions (confidence, binary/multiclass Brier, prob-vector
reconstruction) are kept identical to scripts/diagnostics.py so the numbers
line up with every other surface. This script is read-only over existing
files and only writes reports/abstain_sweep.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.brier import brier_score  # noqa: E402

PRED_PATH = REPO_ROOT / "data" / "predictions" / "ablation_search_brave_fresh.json"
RESOLVED_PATH = REPO_ROOT / "data" / "resolved.json"
OUT_PATH = REPO_ROOT / "reports" / "abstain_sweep.json"

# Sweep 0.50, 0.55, ... 0.90 inclusive.
TAU_GRID = [round(0.50 + 0.05 * i, 2) for i in range(9)]

CAVEAT = (
    "Uniform-0.5 (binary) / 1-over-n (multiclass) is only a PROXY for the "
    "real abstain fallback, which is the MARKET PRICE. Market prices are "
    "unavailable in this offline resolved set, so the no-skill uniform "
    "fallback is a conservative lower bound on the benefit of confidence "
    "gating -- a market-price fallback would likely beat uniform and so "
    "improve the gated Brier further."
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def _pred_rows(payload: Any) -> list[dict]:
    rows = payload.get("predictions", payload) if isinstance(payload, dict) else payload
    return [r for r in rows if isinstance(r, dict)]


def _winner(ev: dict) -> str | None:
    ro = ev.get("resolved_outcome") or {}
    val = ro.get("value") if isinstance(ro, dict) else None
    return val[0] if isinstance(val, list) and val else None


def _prob_vector(row: dict, outcomes: list[str]) -> list[float]:
    """Per-outcome probabilities, defensively reconstructed.

    Matches scripts/diagnostics.py so confidence and Brier numbers align.
    """
    probs = row.get("probabilities")
    if isinstance(probs, list) and probs and isinstance(probs[0], dict):
        by_market = {str(p.get("market")): float(p.get("probability", 0.0)) for p in probs}
        return [by_market.get(o, 0.0) for o in outcomes]
    p_yes = float(row.get("p_yes", 1.0 / max(len(outcomes), 1)))
    if len(outcomes) <= 1:
        return [p_yes]
    rest = (1.0 - p_yes) / (len(outcomes) - 1)
    return [p_yes] + [rest] * (len(outcomes) - 1)


def _multi_brier(vec: list[float], wi: int) -> float:
    return sum((p - (1.0 if i == wi else 0.0)) ** 2 for i, p in enumerate(vec))


def build_records(pred_path: Path, resolved_path: Path) -> list[dict]:
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
        vec = _prob_vector(preds[tk], outcomes)
        if not vec:
            continue
        wi = outcomes.index(winner)
        n = len(outcomes)

        # model forecast (kept when confidence >= tau)
        p_yes = min(max(vec[0], 0.0), 1.0)
        actual0 = 1 if wi == 0 else 0
        model_binary = brier_score(p_yes, actual0)
        model_multi = _multi_brier(vec, wi)

        # no-skill uniform fallback (used when confidence < tau)
        uniform_vec = [1.0 / n] * n
        fallback_binary = brier_score(0.5, actual0)
        fallback_multi = _multi_brier(uniform_vec, wi)

        records.append({
            "ticker": tk,
            "n_outcomes": n,
            "kind": "binary" if n == 2 else "multi",
            "confidence": max(vec),
            "model_binary_brier": model_binary,
            "model_multi_brier": model_multi,
            "fallback_binary_brier": fallback_binary,
            "fallback_multi_brier": fallback_multi,
        })
    return records


def evaluate_at_tau(records: list[dict], tau: float) -> dict:
    """Gate at threshold tau: keep forecast if confidence >= tau, else fall back."""
    n = len(records)
    n_kept = 0
    bin_sum = 0.0
    multi_sum = 0.0
    for r in records:
        keep = r["confidence"] >= tau
        if keep:
            n_kept += 1
            bin_sum += r["model_binary_brier"]
            multi_sum += r["model_multi_brier"]
        else:
            bin_sum += r["fallback_binary_brier"]
            multi_sum += r["fallback_multi_brier"]
    return {
        "tau": tau,
        "n_kept": n_kept,
        "n_abstained": n - n_kept,
        "frac_kept": n_kept / n if n else 0.0,
        "mean_binary_brier": bin_sum / n if n else 0.0,
        "mean_multi_brier": multi_sum / n if n else 0.0,
    }


def main() -> int:
    records = build_records(PRED_PATH, RESOLVED_PATH)
    n = len(records)
    if n == 0:
        print("No joined records found; nothing to sweep.")
        return 1

    min_conf = min(r["confidence"] for r in records)

    # Baseline = ungated (keep everything). Use a threshold at/below min conf.
    baseline_tau = min(min_conf, 0.5)
    baseline = evaluate_at_tau(records, baseline_tau)

    curve = [evaluate_at_tau(records, tau) for tau in TAU_GRID]

    # tau* minimises binary Brier (tie-break: smaller tau = fewer abstentions).
    best = min(curve, key=lambda c: (c["mean_binary_brier"], c["tau"]))
    delta = baseline["mean_binary_brier"] - best["mean_binary_brier"]
    helped = delta > 1e-9

    report = {
        "source_predictions": str(PRED_PATH.relative_to(REPO_ROOT)),
        "source_resolved": str(RESOLVED_PATH.relative_to(REPO_ROOT)),
        "n_events": n,
        "n_binary": sum(1 for r in records if r["kind"] == "binary"),
        "n_multi": sum(1 for r in records if r["kind"] == "multi"),
        "min_confidence": min_conf,
        "fallback": "uniform (binary p_yes -> 0.5; multiclass -> 1/n)",
        "baseline": {
            "tau": baseline["tau"],
            "description": "ungated -- every forecast kept",
            "mean_binary_brier": baseline["mean_binary_brier"],
            "mean_multi_brier": baseline["mean_multi_brier"],
        },
        "tau_grid": TAU_GRID,
        "curve": curve,
        "tau_star": best["tau"],
        "gated_binary_brier_at_tau_star": best["mean_binary_brier"],
        "gated_multi_brier_at_tau_star": best["mean_multi_brier"],
        "n_kept_at_tau_star": best["n_kept"],
        "n_abstained_at_tau_star": best["n_abstained"],
        "binary_brier_improvement": delta,
        "gating_helped": helped,
        "caveat": CAVEAT,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2) + "\n")

    # ---- console table ----
    print("Confidence-gated abstain sweep")
    print(f"predictions: {report['source_predictions']}")
    print(f"events: {n} (binary {report['n_binary']}, multi {report['n_multi']})  "
          f"min confidence {min_conf:.2f}")
    print(f"fallback: {report['fallback']}")
    print()
    print(f"{'tau':>6} | {'kept':>5} | {'abst':>5} | {'frac_kept':>9} | "
          f"{'binBrier':>9} | {'multiBrier':>10}")
    print("-" * 62)
    for c in curve:
        mark = "  <- tau*" if c["tau"] == best["tau"] else ""
        print(f"{c['tau']:>6.2f} | {c['n_kept']:>5} | {c['n_abstained']:>5} | "
              f"{c['frac_kept']:>9.3f} | {c['mean_binary_brier']:>9.4f} | "
              f"{c['mean_multi_brier']:>10.4f}{mark}")
    print("-" * 62)
    print(f"baseline (ungated, tau={baseline['tau']:.2f}): "
          f"binary Brier {baseline['mean_binary_brier']:.4f}  "
          f"multi Brier {baseline['mean_multi_brier']:.4f}")
    print(f"tau* = {best['tau']:.2f} -> gated binary Brier "
          f"{best['mean_binary_brier']:.4f} "
          f"(multi {best['mean_multi_brier']:.4f}); "
          f"kept {best['n_kept']}/{n}, abstained {best['n_abstained']}")
    if helped:
        rel = 100.0 * delta / baseline["mean_binary_brier"]
        print(f"gating HELPED: binary Brier improved by {delta:.4f} "
              f"({rel:.1f}% lower) vs ungated baseline")
    else:
        print("gating did NOT help: no tau beats the ungated baseline binary Brier")
    print()
    print("CAVEAT: " + CAVEAT)
    print(f"\nwrote {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
