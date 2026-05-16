"""Build the post-event results report for one experiment slug.

Reads ``trace/<slug>/*.jsonl`` and writes PNG plots plus a summary.json
into ``reports/<slug>/``.

Stdlib + matplotlib + pandas. Invoked post-event:

    python scripts/build_results_report.py --slug my-slug

Handles the empty-trace case gracefully: prints a notice and exits 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def _trace_dir(slug: str, trace_root: str) -> Path:
    return Path(trace_root) / slug


def _report_dir(slug: str, report_root: str) -> Path:
    p = Path(report_root) / slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_trace(slug: str, trace_root: str = "trace") -> pd.DataFrame:
    """Concat every JSONL file under trace/<slug>/ into one frame.

    Returns an empty DataFrame if no files are present.
    """
    d = _trace_dir(slug, trace_root)
    if not d.exists():
        return pd.DataFrame()
    rows: list[dict] = []
    for path in sorted(d.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def plot_calibration(df: pd.DataFrame, out: Path) -> None:
    """Predicted vs realized YES rate per bucket, with simple error bars.

    Requires a ``resolved_yes`` boolean column. Many traces will not have
    that until resolution data is joined in; in that case, we skip with a
    note instead of failing.
    """
    if "resolved_yes" not in df.columns or "probability_bucket" not in df.columns:
        print("  calibration_plot: skipped (no resolved_yes or probability_bucket)")
        return
    sub = df.dropna(subset=["resolved_yes", "probability_bucket"])
    if sub.empty:
        print("  calibration_plot: skipped (no resolved rows)")
        return
    grouped = sub.groupby("probability_bucket")["resolved_yes"].agg(["mean", "count", "std"])
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], linestyle="--", alpha=0.5, label="perfect calibration")
    se = (grouped["std"] / grouped["count"].pow(0.5)).fillna(0.0)
    ax.errorbar(grouped.index, grouped["mean"], yerr=se, marker="o",
                linestyle="-", capsize=4, label="agent")
    ax.set_xlabel("predicted probability bucket")
    ax.set_ylabel("realized YES rate")
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_title("Calibration")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "calibration_plot.png", dpi=150)
    plt.close(fig)


def plot_brier_per_market_class(df: pd.DataFrame, out: Path) -> None:
    """Brier by market category. Requires ``category`` and ``resolved_yes`` columns."""
    if not {"category", "resolved_yes", "p_yes"}.issubset(df.columns):
        print("  brier_per_market_class: skipped (need category, resolved_yes, p_yes)")
        return
    sub = df.dropna(subset=["category", "resolved_yes", "p_yes"])
    if sub.empty:
        print("  brier_per_market_class: skipped (no resolved rows with category)")
        return
    sub = sub.assign(brier=(sub["p_yes"] - sub["resolved_yes"].astype(float)) ** 2)
    grouped = sub.groupby("category")["brier"].mean().sort_values()
    fig, ax = plt.subplots(figsize=(8, max(3, 0.4 * len(grouped))))
    grouped.plot(kind="barh", ax=ax)
    ax.set_xlabel("Brier (lower is better)")
    ax.set_title("Brier by market class")
    fig.tight_layout()
    fig.savefig(out / "brier_per_market_class.png", dpi=150)
    plt.close(fig)


def plot_bankroll_curve(df: pd.DataFrame, out: Path, starting_bankroll: float = 10000.0) -> None:
    """Running bankroll over ticks.

    Skeleton estimate: bankroll - cumulative_notional + cumulative_realized_pnl.
    Realized PnL column is optional; falls back to notional-only if absent.
    """
    if "timestamp" not in df.columns:
        print("  bankroll_curve: skipped (no timestamp)")
        return
    sub = df.copy()
    sub["timestamp"] = pd.to_datetime(sub["timestamp"], errors="coerce")
    sub = sub.dropna(subset=["timestamp"]).sort_values("timestamp")
    if sub.empty:
        print("  bankroll_curve: skipped (no timestamped rows)")
        return
    notional = sub.get("notional", pd.Series(0.0, index=sub.index)).fillna(0.0)
    realized = sub.get("realized_pnl", pd.Series(0.0, index=sub.index)).fillna(0.0)
    cum_notional = notional.cumsum()
    cum_realized = realized.cumsum()
    bankroll = starting_bankroll - cum_notional + cum_realized
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(sub["timestamp"], bankroll, marker=".", linestyle="-")
    ax.axhline(starting_bankroll, linestyle="--", alpha=0.4, label="starting bankroll")
    ax.set_xlabel("time")
    ax.set_ylabel("bankroll (USD)")
    ax.set_title("Bankroll over time")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out / "bankroll_curve.png", dpi=150)
    plt.close(fig)


def plot_edge_distribution(df: pd.DataFrame, out: Path) -> None:
    """Histogram of edge captured vs edge forecasted, on traded rows only."""
    if not {"yes_edge", "no_edge", "action"}.issubset(df.columns):
        print("  edge_distribution: skipped (need yes_edge, no_edge, action)")
        return
    sub = df[df["action"] == "BUY"].copy()
    if sub.empty:
        print("  edge_distribution: skipped (no BUY rows)")
        return
    forecasted = sub.apply(
        lambda r: r["yes_edge"] if r.get("side") == "YES" else r["no_edge"],
        axis=1,
    )
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(forecasted.dropna(), bins=20, alpha=0.7, label="forecasted edge")
    if "captured_edge" in sub.columns:
        ax.hist(sub["captured_edge"].dropna(), bins=20, alpha=0.5, label="captured edge")
    ax.set_xlabel("edge")
    ax.set_ylabel("count")
    ax.set_title("Edge distribution (BUYs only)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "edge_distribution.png", dpi=150)
    plt.close(fig)


def plot_skip_reasons(df: pd.DataFrame, out: Path) -> None:
    if "skip_reason" not in df.columns:
        print("  skip_reasons: skipped (no skip_reason column)")
        return
    sub = df[df["skip_reason"].notna() & (df["skip_reason"] != "")]
    if sub.empty:
        print("  skip_reasons: skipped (no skip rows)")
        return
    counts = sub["skip_reason"].value_counts().head(10)
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.pie(counts.values, labels=counts.index.astype(str), autopct="%1.0f%%",
           startangle=90)
    ax.set_title("Skip reasons (top 10)")
    fig.tight_layout()
    fig.savefig(out / "skip_reasons.png", dpi=150)
    plt.close(fig)


def write_summary(df: pd.DataFrame, out: Path, starting_bankroll: float = 10000.0) -> None:
    summary: dict = {
        "total_records": int(len(df)),
        "ticks_seen": int(df["tick_id"].nunique()) if "tick_id" in df.columns else 0,
        "trades": int((df.get("action") == "BUY").sum()) if "action" in df.columns else 0,
        "skips": int((df.get("action") == "SKIP").sum()) if "action" in df.columns else 0,
        "starting_bankroll": starting_bankroll,
    }
    if {"p_yes", "resolved_yes"}.issubset(df.columns):
        sub = df.dropna(subset=["p_yes", "resolved_yes"])
        if not sub.empty:
            brier = ((sub["p_yes"] - sub["resolved_yes"].astype(float)) ** 2).mean()
            summary["brier"] = float(brier)
            summary["brier_n"] = int(len(sub))
    if "notional" in df.columns and "action" in df.columns:
        notional_traded = float(df.loc[df["action"] == "BUY", "notional"].fillna(0.0).sum())
        summary["total_notional_traded"] = notional_traded
    if "realized_pnl" in df.columns:
        realized = float(df["realized_pnl"].fillna(0.0).sum())
        summary["realized_pnl"] = realized
        summary["final_bankroll_estimate"] = (
            starting_bankroll
            - float(df.get("notional", pd.Series(0.0)).fillna(0.0).sum())
            + realized
        )
    out_path = out / "summary.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"  summary.json -> {out_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build post-event results report from JSONL traces.")
    parser.add_argument("--slug", required=True, help="experiment slug under trace/")
    parser.add_argument("--trace-root", default="trace")
    parser.add_argument("--report-root", default="reports")
    parser.add_argument("--starting-bankroll", type=float, default=10000.0)
    args = parser.parse_args(argv)

    df = load_trace(args.slug, args.trace_root)
    out = _report_dir(args.slug, args.report_root)

    if df.empty:
        print(f"no trace records for slug={args.slug!r}; skipping plots")
        write_summary(df, out, args.starting_bankroll)
        return 0

    print(f"loaded {len(df)} records across {df['tick_id'].nunique() if 'tick_id' in df.columns else 0} ticks")
    plot_calibration(df, out)
    plot_brier_per_market_class(df, out)
    plot_bankroll_curve(df, out, args.starting_bankroll)
    plot_edge_distribution(df, out)
    plot_skip_reasons(df, out)
    write_summary(df, out, args.starting_bankroll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
