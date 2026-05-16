"""Auto-refreshing HTML dashboard for the prophet-hacks JSONL trace.

Reads every ``*.jsonl`` file under the trace directory, aggregates the
records into portfolio-grade stats, renders an HTML page with embedded
SVG charts, and rewrites the page on a fixed interval. The page uses a
``<meta http-equiv="refresh">`` tag so any browser session pointed at it
stays current without WebSockets, Flask, or any server-push machinery.

Schema-tolerant by design: every per-record field is read with
``.get(...)`` so traces written before the v2 calibrated pipeline lands
(no ``p_market`` / ``p_model_raw`` / ``decomposition_json`` / etc.) still
render. Records with optional v2 fields enable the calibration and
disagreement panels automatically.

Usage:
    python monitor/live_monitor.py --output traces/live.html --refresh 5
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


log = logging.getLogger("prophet-hacks.monitor")


# ---------------------------------------------------------------------------
# Data ingest
# ---------------------------------------------------------------------------


def iter_records(trace_dir: Path) -> Iterable[dict]:
    """Yield every JSON record under ``trace_dir/**/*.jsonl``.

    Malformed lines are skipped with a debug log. Files that disappear
    between glob and open (the agent may rotate them) are tolerated.
    """
    for path in sorted(trace_dir.rglob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as e:
                        log.debug("skipping malformed line %s:%d: %s", path, lineno, e)
        except FileNotFoundError:
            continue


# ---------------------------------------------------------------------------
# Aggregations (pure)
# ---------------------------------------------------------------------------


@dataclass
class Aggregate:
    total_records: int = 0
    buy_count: int = 0
    skip_count: int = 0
    total_notional: float = 0.0
    total_cost_usd: float = 0.0
    edges_taken: list[float] = field(default_factory=list)
    skip_reasons: Counter = field(default_factory=Counter)
    providers: Counter = field(default_factory=Counter)
    domains: Counter = field(default_factory=Counter)
    p_yes: list[float] = field(default_factory=list)
    disagreements: list[float] = field(default_factory=list)
    tick_ids: set[str] = field(default_factory=set)
    recent: list[dict] = field(default_factory=list)
    timestamps: list[datetime] = field(default_factory=list)
    last_timestamp: Optional[str] = None


def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _short_skip(reason: Optional[str]) -> str:
    if not reason:
        return "(none)"
    head = reason.split(":", 1)[0].strip()
    return head[:60] or "(unspecified)"


def aggregate(records: Iterable[dict], recent_n: int = 20) -> Aggregate:
    """Walk records once and compute every panel's input."""
    agg = Aggregate()
    for rec in records:
        agg.total_records += 1
        action = (rec.get("action") or "").upper()
        if action == "BUY":
            agg.buy_count += 1
        elif action == "SKIP":
            agg.skip_count += 1
            agg.skip_reasons[_short_skip(rec.get("skip_reason"))] += 1

        notional = rec.get("notional")
        if isinstance(notional, (int, float)):
            agg.total_notional += float(notional)
        cost = rec.get("cost_estimate_usd")
        if isinstance(cost, (int, float)):
            agg.total_cost_usd += float(cost)
        provider = rec.get("model_provider") or "unknown"
        agg.providers[provider] += 1
        domain = rec.get("domain") or "unknown"
        agg.domains[domain] += 1

        p_yes = rec.get("p_yes")
        if isinstance(p_yes, (int, float)):
            agg.p_yes.append(float(p_yes))

        dstd = rec.get("disagreement_stdev")
        if isinstance(dstd, (int, float)):
            agg.disagreements.append(float(dstd))

        ye = rec.get("yes_edge")
        ne = rec.get("no_edge")
        if action == "BUY":
            edge = ye if (rec.get("side") or "").upper() == "YES" else ne
            if isinstance(edge, (int, float)):
                agg.edges_taken.append(float(edge))

        tid = rec.get("tick_id")
        if tid:
            agg.tick_ids.add(tid)
        ts = _parse_ts(rec.get("timestamp", ""))
        if ts is not None:
            agg.timestamps.append(ts)
        if rec.get("timestamp"):
            agg.last_timestamp = rec["timestamp"]

        agg.recent.append(rec)

    # Keep only the most recent N for the table; sorted by timestamp desc.
    agg.recent.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    agg.recent = agg.recent[:recent_n]
    return agg


def tick_latency_quantiles(timestamps: list[datetime]) -> dict:
    """Approx p50/p95 of inter-record gaps in seconds. Empty -> zeros."""
    if len(timestamps) < 2:
        return {"p50": 0.0, "p95": 0.0, "n": len(timestamps)}
    ordered = sorted(timestamps)
    gaps = [
        (ordered[i] - ordered[i - 1]).total_seconds()
        for i in range(1, len(ordered))
    ]
    gaps = [g for g in gaps if g >= 0]
    if not gaps:
        return {"p50": 0.0, "p95": 0.0, "n": len(timestamps)}
    gaps_sorted = sorted(gaps)
    p50 = gaps_sorted[len(gaps_sorted) // 2]
    p95_idx = max(0, int(0.95 * len(gaps_sorted)) - 1)
    p95 = gaps_sorted[p95_idx]
    return {"p50": p50, "p95": p95, "n": len(timestamps)}


# ---------------------------------------------------------------------------
# Chart rendering (matplotlib -> embedded SVG)
# ---------------------------------------------------------------------------


def _render_svg(plot_fn) -> str:
    """Run plot_fn(ax) and return a base64-embedded ``<img>`` data URI."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.5, 3.4), dpi=110)
    fig.patch.set_facecolor("#11151c")
    ax.set_facecolor("#11151c")
    for spine in ax.spines.values():
        spine.set_color("#3a4151")
    ax.tick_params(colors="#c8cfdb")
    ax.yaxis.label.set_color("#c8cfdb")
    ax.xaxis.label.set_color("#c8cfdb")
    ax.title.set_color("#e8edf5")
    plot_fn(ax)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="svg", facecolor=fig.get_facecolor())
    plt.close(fig)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def chart_skip_reasons(agg: Aggregate) -> str:
    items = agg.skip_reasons.most_common(8)
    if not items:
        return _render_svg(lambda ax: ax.text(
            0.5, 0.5, "no skips yet", ha="center", va="center",
            color="#c8cfdb", transform=ax.transAxes,
        ))
    labels = [k for k, _ in items][::-1]
    values = [v for _, v in items][::-1]

    def plot(ax):
        ax.barh(labels, values, color="#f0a868")
        ax.set_title("Skip reasons (top 8)")
        ax.set_xlabel("count")
    return _render_svg(plot)


def chart_p_yes_distribution(agg: Aggregate) -> str:
    if not agg.p_yes:
        return _render_svg(lambda ax: ax.text(
            0.5, 0.5, "no forecasts yet", ha="center", va="center",
            color="#c8cfdb", transform=ax.transAxes,
        ))

    def plot(ax):
        ax.hist(agg.p_yes, bins=10, range=(0.0, 1.0), color="#64a8f8",
                edgecolor="#11151c")
        ax.set_title("p_yes distribution")
        ax.set_xlabel("p_yes")
        ax.set_ylabel("count")
    return _render_svg(plot)


def chart_provider_mix(agg: Aggregate) -> str:
    items = agg.providers.most_common()
    if not items:
        return _render_svg(lambda ax: ax.text(
            0.5, 0.5, "no provider records yet", ha="center", va="center",
            color="#c8cfdb", transform=ax.transAxes,
        ))
    labels = [k for k, _ in items]
    values = [v for _, v in items]

    def plot(ax):
        ax.pie(values, labels=labels, autopct="%1.0f%%",
               colors=["#f0a868", "#64a8f8", "#7cd99f", "#d97c7c", "#b88af0"],
               textprops={"color": "#c8cfdb"})
        ax.set_title("Decisions by model provider")
    return _render_svg(plot)


def chart_disagreement(agg: Aggregate) -> str:
    if not agg.disagreements:
        return _render_svg(lambda ax: ax.text(
            0.5, 0.5,
            "no disagreement signal yet (v2 pipeline writes this)",
            ha="center", va="center", color="#c8cfdb", transform=ax.transAxes,
        ))

    def plot(ax):
        ax.hist(agg.disagreements, bins=15, color="#b88af0",
                edgecolor="#11151c")
        ax.set_title("Model disagreement (ensemble stdev)")
        ax.set_xlabel("stdev")
        ax.set_ylabel("count")
    return _render_svg(plot)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _template_path() -> Path:
    return Path(__file__).parent / "template.html.j2"


def render_html(
    agg: Aggregate,
    trace_dir: Path,
    refresh_seconds: int,
) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(
        loader=FileSystemLoader(str(_template_path().parent)),
        autoescape=select_autoescape(["html"]),
    )
    tpl = env.get_template(_template_path().name)

    latency = tick_latency_quantiles(agg.timestamps)
    avg_edge = (
        sum(agg.edges_taken) / len(agg.edges_taken) if agg.edges_taken else 0.0
    )
    win_rate_proxy = (
        agg.buy_count / (agg.buy_count + agg.skip_count)
        if (agg.buy_count + agg.skip_count) > 0 else 0.0
    )

    cards = [
        ("Ticks observed", f"{len(agg.tick_ids):,}"),
        ("Decisions", f"{agg.total_records:,}"),
        ("BUY", f"{agg.buy_count:,}"),
        ("SKIP", f"{agg.skip_count:,}"),
        ("Trade rate", f"{win_rate_proxy * 100:.1f}%"),
        ("Notional deployed", f"${agg.total_notional:,.2f}"),
        ("Avg edge taken", f"{avg_edge:+.3f}"),
        ("LLM cost", f"${agg.total_cost_usd:,.4f}"),
        ("Tick gap p50", f"{latency['p50']:.1f}s"),
        ("Tick gap p95", f"{latency['p95']:.1f}s"),
    ]

    return tpl.render(
        cards=cards,
        recent=agg.recent,
        chart_skips=chart_skip_reasons(agg),
        chart_p_yes=chart_p_yes_distribution(agg),
        chart_providers=chart_provider_mix(agg),
        chart_disagreement=chart_disagreement(agg),
        last_timestamp=agg.last_timestamp or "(none)",
        trace_dir=str(trace_dir),
        refresh_seconds=refresh_seconds,
        rendered_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live HTML dashboard for prophet-hacks JSONL trace.",
    )
    p.add_argument(
        "--trace-dir",
        default=os.getenv("PROPHET_TRACE_PATH", "trace"),
        help="Root directory containing JSONL traces (recursive).",
    )
    p.add_argument(
        "--output", default="trace/live.html",
        help="HTML file to write on every refresh.",
    )
    p.add_argument(
        "--refresh", type=int, default=5,
        help="Seconds between re-renders. Also used as meta-refresh interval.",
    )
    p.add_argument(
        "--once", action="store_true",
        help="Render exactly once and exit (useful for post-event reporting).",
    )
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def render_once(trace_dir: Path, output: Path, refresh_seconds: int) -> None:
    agg = aggregate(iter_records(trace_dir))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_html(agg, trace_dir, refresh_seconds), encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    trace_dir = Path(args.trace_dir)
    output = Path(args.output)

    log.info(
        "monitor starting: trace_dir=%s output=%s refresh=%ds",
        trace_dir, output, args.refresh,
    )

    if args.once:
        render_once(trace_dir, output, args.refresh)
        return 0

    try:
        while True:
            try:
                render_once(trace_dir, output, args.refresh)
            except Exception:
                log.exception("render failed (will retry next cycle)")
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        log.info("monitor stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
