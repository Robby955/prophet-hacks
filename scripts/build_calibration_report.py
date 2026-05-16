"""Build calibration plots and a per-outcome Brier summary across variants.

Reads:
  - data/resolved.json                    -- 26 events with outcomes + resolved_outcome.value
  - data/predictions/*.json               -- one file per variant, each with `predictions`

Emits:
  - reports/calibration.png               -- aggregated reliability diagram (10 bins)
  - reports/calibration_by_category.png   -- one subplot per category (>= 3 events)
  - reports/per_outcome_brier.png         -- bar chart of mean per-outcome Brier
  - reports/calibration_summary.md        -- markdown table: variant, n, Brier, ECE,
                                             best/worst categories

Per-event proper Brier is:
    sum_i (p_i - 1[outcome_i == winner])^2
where winner = resolved_outcome.value[0].

Probability vectors:
  - If a prediction includes an explicit ``probabilities`` list of the same
    length as outcomes, use it (normalised defensively).
  - Otherwise treat the prediction as binary-style: outcomes[0] gets ``p_yes``,
    the remaining outcomes split (1 - p_yes) / (n - 1).

Stdlib + matplotlib only. Run from repo root::

    .venv/bin/python scripts/build_calibration_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


# --- skip files in data/predictions/ that aren't variant prediction files ---
NON_VARIANT_FILES = {"backtest_summary.json"}

# Variants we want to highlight at the front of plots/tables (in order).
PRIMARY_ORDER = ["uniform_prior", "single_llm", "multi_outcome"]


# ---------- IO ----------------------------------------------------------------

def load_resolved(path: Path) -> list[dict]:
    """Load resolved events; skip rows lacking a resolved_outcome winner."""
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    cleaned: list[dict] = []
    for ev in data:
        winners = (ev.get("resolved_outcome") or {}).get("value") or []
        outcomes = ev.get("outcomes") or []
        if not winners or not outcomes:
            continue
        if winners[0] not in outcomes:
            # Skip events whose winner label is not one of the listed outcomes.
            continue
        cleaned.append(ev)
    return cleaned


def load_variant(path: Path) -> dict[str, dict]:
    """Return {market_ticker: prediction_dict} for one variant file."""
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    preds = data.get("predictions") if isinstance(data, dict) else None
    if not isinstance(preds, list):
        return {}
    out: dict[str, dict] = {}
    for p in preds:
        if not isinstance(p, dict):
            continue
        mt = p.get("market_ticker")
        if mt:
            out[mt] = p
    return out


# ---------- probability distribution -----------------------------------------

def probability_vector(prediction: dict, outcomes: list[str]) -> list[float] | None:
    """Return a probability vector aligned with ``outcomes``.

    Uses an explicit ``probabilities`` list if present and well-formed,
    otherwise distributes ``p_yes`` across outcomes (outcomes[0] gets p_yes,
    the rest evenly share the residual).
    """
    n = len(outcomes)
    if n == 0:
        return None

    explicit = prediction.get("probabilities")
    if isinstance(explicit, list) and len(explicit) == n:
        try:
            vals = [float(x) for x in explicit]
        except (TypeError, ValueError):
            vals = None
        if vals is not None:
            total = sum(vals)
            if total > 0:
                return [v / total for v in vals]

    raw = prediction.get("p_yes")
    if raw is None:
        return None
    try:
        p_yes = float(raw)
    except (TypeError, ValueError):
        return None
    p_yes = max(0.0, min(1.0, p_yes))

    if n == 1:
        return [1.0]
    residual = (1.0 - p_yes) / (n - 1)
    return [p_yes] + [residual] * (n - 1)


# ---------- scoring -----------------------------------------------------------

def score_event(prob: list[float], outcomes: list[str], winner: str) -> dict:
    """Compute per-event Brier and winner probability."""
    brier = 0.0
    p_winner = 0.0
    for outcome, p in zip(outcomes, prob):
        target = 1.0 if outcome == winner else 0.0
        brier += (p - target) ** 2
        if outcome == winner:
            p_winner = p
    return {"brier": brier, "p_winner": p_winner}


def score_variant(
    variant_preds: dict[str, dict],
    resolved: list[dict],
) -> list[dict]:
    """Return list of per-event score rows for one variant."""
    rows: list[dict] = []
    for ev in resolved:
        mt = ev.get("market_ticker")
        pred = variant_preds.get(mt)
        if pred is None:
            continue
        outcomes = ev["outcomes"]
        winner = ev["resolved_outcome"]["value"][0]
        prob = probability_vector(pred, outcomes)
        if prob is None:
            continue
        result = score_event(prob, outcomes, winner)
        rows.append(
            {
                "market_ticker": mt,
                "category": ev.get("category") or "Unknown",
                "n_outcomes": len(outcomes),
                "p_winner": result["p_winner"],
                "brier": result["brier"],
            }
        )
    return rows


# ---------- reliability/ECE --------------------------------------------------

def reliability_from_pairs(
    pairs: list[tuple[float, int]], n_bins: int = 10
) -> dict:
    """Standard multi-class reliability binning.

    ``pairs`` is a list of (predicted_probability, is_correct_indicator).
    Returns dict with bin centers, mean predicted, empirical accuracy, counts.
    """
    edges = [i / n_bins for i in range(n_bins + 1)]
    bin_p: list[list[float]] = [[] for _ in range(n_bins)]
    bin_y: list[list[int]] = [[] for _ in range(n_bins)]
    for p, y in pairs:
        # Clamp to [0, 1] then bin. The last bin includes 1.0.
        pc = max(0.0, min(1.0, p))
        idx = int(pc * n_bins)
        if idx == n_bins:
            idx = n_bins - 1
        bin_p[idx].append(pc)
        bin_y[idx].append(y)

    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(n_bins)]
    mean_p = [sum(b) / len(b) if b else float("nan") for b in bin_p]
    emp = [sum(b) / len(b) if b else float("nan") for b in bin_y]
    counts = [len(b) for b in bin_p]
    return {
        "edges": edges,
        "centers": centers,
        "mean_p": mean_p,
        "empirical": emp,
        "counts": counts,
    }


def ece_from_pairs(pairs: list[tuple[float, int]], n_bins: int = 10) -> float:
    """Expected Calibration Error (count-weighted abs gap)."""
    if not pairs:
        return float("nan")
    rb = reliability_from_pairs(pairs, n_bins=n_bins)
    total = sum(rb["counts"])
    if total == 0:
        return float("nan")
    ece = 0.0
    for w, p, e in zip(rb["counts"], rb["mean_p"], rb["empirical"]):
        if w == 0 or p != p or e != e:  # skip empty/NaN bins
            continue
        ece += (w / total) * abs(p - e)
    return ece


def event_pairs(
    variant_preds: dict[str, dict],
    resolved: list[dict],
) -> list[tuple[float, int, str]]:
    """Flatten every (outcome, predicted probability, is_winner, category) row.

    The returned tuples are (p, is_winner, category). This is the input shape
    expected by reliability_from_pairs (the third element is used by
    by-category plots).
    """
    out: list[tuple[float, int, str]] = []
    for ev in resolved:
        mt = ev.get("market_ticker")
        pred = variant_preds.get(mt)
        if pred is None:
            continue
        outcomes = ev["outcomes"]
        winner = ev["resolved_outcome"]["value"][0]
        prob = probability_vector(pred, outcomes)
        if prob is None:
            continue
        category = ev.get("category") or "Unknown"
        for outcome, p in zip(outcomes, prob):
            y = 1 if outcome == winner else 0
            out.append((float(p), y, category))
    return out


# ---------- plotting ---------------------------------------------------------

def _variant_color(idx: int) -> str:
    """Stable color cycle for variants."""
    cycle = plt.rcParams["axes.prop_cycle"].by_key().get("color", [])
    if not cycle:
        return "C0"
    return cycle[idx % len(cycle)]


def plot_aggregate_calibration(
    variant_pairs: dict[str, list[tuple[float, int, str]]],
    out_path: Path,
    n_bins: int = 10,
) -> None:
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.plot([0, 1], [0, 1], linestyle="--", color="gray", alpha=0.6,
            label="perfectly calibrated")

    for idx, (variant, pairs) in enumerate(variant_pairs.items()):
        rb = reliability_from_pairs(
            [(p, y) for (p, y, _c) in pairs], n_bins=n_bins
        )
        xs, ys = [], []
        for p, e in zip(rb["mean_p"], rb["empirical"]):
            if p == p and e == e:  # not NaN
                xs.append(p)
                ys.append(e)
        ax.plot(xs, ys, marker="o", linestyle="-",
                color=_variant_color(idx), label=variant)

    ax.set_xlabel("predicted probability (bin mean)")
    ax.set_ylabel("empirical fraction correct")
    ax.set_title("Reliability diagram (per-outcome, 10 bins)")
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", fontsize=8)
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def plot_calibration_by_category(
    variant_pairs: dict[str, list[tuple[float, int, str]]],
    resolved: list[dict],
    out_path: Path,
    n_bins: int = 10,
    min_events: int = 3,
) -> None:
    # Count distinct events per category (not per-pair).
    cat_event_counts: dict[str, int] = {}
    for ev in resolved:
        c = ev.get("category") or "Unknown"
        cat_event_counts[c] = cat_event_counts.get(c, 0) + 1
    cats = sorted(
        [c for c, n in cat_event_counts.items() if n >= min_events]
    )
    if not cats:
        print("  calibration_by_category: no categories with >= 3 events; "
              "writing placeholder.")
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "No categories with >= 3 events",
                ha="center", va="center")
        ax.axis("off")
        fig.savefig(out_path, dpi=160, bbox_inches="tight")
        plt.close(fig)
        return

    ncols = min(2, len(cats))
    nrows = (len(cats) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols,
                             figsize=(6 * ncols, 5 * nrows),
                             squeeze=False)

    for ax_idx, category in enumerate(cats):
        ax = axes[ax_idx // ncols][ax_idx % ncols]
        ax.plot([0, 1], [0, 1], linestyle="--", color="gray", alpha=0.6,
                label="perfect")
        for vidx, (variant, pairs) in enumerate(variant_pairs.items()):
            cat_pairs = [(p, y) for (p, y, c) in pairs if c == category]
            if not cat_pairs:
                continue
            rb = reliability_from_pairs(cat_pairs, n_bins=n_bins)
            xs, ys = [], []
            for p, e in zip(rb["mean_p"], rb["empirical"]):
                if p == p and e == e:
                    xs.append(p)
                    ys.append(e)
            ax.plot(xs, ys, marker="o", linestyle="-",
                    color=_variant_color(vidx), label=variant)
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.0)
        ax.set_xlabel("predicted probability")
        ax.set_ylabel("empirical fraction correct")
        ax.set_title(
            f"{category} (n={cat_event_counts[category]} events)"
        )
        ax.grid(True, alpha=0.25)
        ax.legend(loc="upper left", fontsize=7)

    # Blank out any unused axes.
    for j in range(len(cats), nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")

    fig.suptitle("Reliability by category", y=1.02, fontsize=12)
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def plot_per_outcome_brier(
    variant_brier: dict[str, float],
    variant_n: dict[str, int],
    out_path: Path,
) -> None:
    if not variant_brier:
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "No variants with scored events",
                ha="center", va="center")
        ax.axis("off")
        fig.savefig(out_path, dpi=160, bbox_inches="tight")
        plt.close(fig)
        return

    # Order: PRIMARY_ORDER first, then the rest sorted by Brier ascending.
    primary = [v for v in PRIMARY_ORDER if v in variant_brier]
    others = sorted(
        [v for v in variant_brier if v not in PRIMARY_ORDER],
        key=lambda v: variant_brier[v],
    )
    variants = primary + others
    values = [variant_brier[v] for v in variants]

    fig, ax = plt.subplots(figsize=(max(8, 0.7 * len(variants) + 4), 5))
    bars = ax.bar(variants, values, color=[_variant_color(i)
                                           for i in range(len(variants))])
    for bar, variant, val in zip(bars, variants, values):
        n = variant_n.get(variant, 0)
        ax.text(bar.get_x() + bar.get_width() / 2, val,
                f"{val:.3f}\n(n={n})",
                ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("mean per-outcome Brier (lower is better)")
    ax.set_title("Per-outcome Brier across variants")
    ax.grid(True, axis="y", alpha=0.25)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    # Give labels some headroom.
    if values:
        ax.set_ylim(0, max(values) * 1.15 + 0.05)
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


# ---------- summary ----------------------------------------------------------

def category_brier(rows: list[dict]) -> dict[str, tuple[float, int]]:
    """{category: (mean_brier, n_events)}."""
    buckets: dict[str, list[float]] = {}
    for r in rows:
        buckets.setdefault(r["category"], []).append(r["brier"])
    return {
        c: (sum(v) / len(v), len(v)) for c, v in buckets.items() if v
    }


def write_summary_markdown(
    variant_rows: dict[str, list[dict]],
    variant_pairs: dict[str, list[tuple[float, int, str]]],
    out_path: Path,
) -> str:
    lines: list[str] = []
    lines.append("# Calibration summary")
    lines.append("")
    lines.append(
        "Per-outcome proper Brier and ECE across forecaster variants, "
        "scored on `data/resolved.json`."
    )
    lines.append("")
    lines.append(
        "| variant | n events | mean Brier | ECE | best category | worst category |"
    )
    lines.append(
        "| --- | ---: | ---: | ---: | --- | --- |"
    )

    # Ordering: PRIMARY_ORDER first, then rest sorted by Brier ascending.
    mean_brier: dict[str, float] = {}
    for variant, rows in variant_rows.items():
        if rows:
            mean_brier[variant] = sum(r["brier"] for r in rows) / len(rows)

    primary = [v for v in PRIMARY_ORDER if v in variant_rows]
    others = sorted(
        [v for v in variant_rows if v not in PRIMARY_ORDER],
        key=lambda v: mean_brier.get(v, float("inf")),
    )
    variants = primary + others

    for variant in variants:
        rows = variant_rows[variant]
        n = len(rows)
        if n == 0:
            lines.append(
                f"| {variant} | 0 | n/a | n/a | n/a | n/a |"
            )
            continue
        mb = mean_brier[variant]
        pairs = [(p, y) for (p, y, _c) in variant_pairs[variant]]
        ece = ece_from_pairs(pairs)
        cb = category_brier(rows)
        # Best = lowest mean Brier; worst = highest. Tie-break by category name.
        sorted_cats = sorted(cb.items(), key=lambda kv: (kv[1][0], kv[0]))
        best = sorted_cats[0]
        worst = sorted_cats[-1]
        best_str = f"{best[0]} ({best[1][0]:.3f}, n={best[1][1]})"
        worst_str = f"{worst[0]} ({worst[1][0]:.3f}, n={worst[1][1]})"
        ece_str = "n/a" if ece != ece else f"{ece:.3f}"
        lines.append(
            f"| {variant} | {n} | {mb:.4f} | {ece_str} | {best_str} | {worst_str} |"
        )

    lines.append("")
    lines.append(
        "Brier is `sum_i (p_i - 1[outcome_i==winner])^2` per event, averaged "
        "across events. ECE is the count-weighted mean absolute gap between "
        "predicted probability and empirical fraction-correct across 10 bins, "
        "computed over every (outcome, p) pair."
    )
    lines.append("")
    text = "\n".join(lines)
    out_path.write_text(text, encoding="utf-8")
    return text


# ---------- entry point ------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    resolved_path = repo_root / "data" / "resolved.json"
    preds_dir = repo_root / "data" / "predictions"
    reports_dir = repo_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    if not resolved_path.exists():
        print(f"resolved.json not found at {resolved_path}", file=sys.stderr)
        return 1
    resolved = load_resolved(resolved_path)
    print(f"loaded {len(resolved)} resolved events from {resolved_path}")

    variant_files = sorted(
        p for p in preds_dir.glob("*.json")
        if p.name not in NON_VARIANT_FILES
    )
    if not variant_files:
        print(f"no variant files in {preds_dir}", file=sys.stderr)
        return 1

    variant_rows: dict[str, list[dict]] = {}
    variant_pairs: dict[str, list[tuple[float, int, str]]] = {}
    for vf in variant_files:
        variant = vf.stem
        preds = load_variant(vf)
        rows = score_variant(preds, resolved)
        pairs = event_pairs(preds, resolved)
        if not rows:
            print(f"  {variant}: no scoreable events; skipping")
            continue
        variant_rows[variant] = rows
        variant_pairs[variant] = pairs
        print(f"  {variant}: scored {len(rows)} events, "
              f"mean Brier={sum(r['brier'] for r in rows)/len(rows):.4f}")

    # Plots.
    plot_aggregate_calibration(
        variant_pairs, reports_dir / "calibration.png"
    )
    print(f"  wrote {reports_dir / 'calibration.png'}")

    plot_calibration_by_category(
        variant_pairs, resolved,
        reports_dir / "calibration_by_category.png",
    )
    print(f"  wrote {reports_dir / 'calibration_by_category.png'}")

    variant_brier = {
        v: sum(r["brier"] for r in rows) / len(rows)
        for v, rows in variant_rows.items()
    }
    variant_n = {v: len(rows) for v, rows in variant_rows.items()}
    plot_per_outcome_brier(
        variant_brier, variant_n,
        reports_dir / "per_outcome_brier.png",
    )
    print(f"  wrote {reports_dir / 'per_outcome_brier.png'}")

    md = write_summary_markdown(
        variant_rows, variant_pairs,
        reports_dir / "calibration_summary.md",
    )
    print(f"  wrote {reports_dir / 'calibration_summary.md'}")
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
