#!/usr/bin/env python3
"""Build a one-page PDF summary for sharing.

The script reads the committed submission report for provenance and writes an
ignored PDF under output/pdf/. It is intentionally separate from
submission/REPORT.md so the hackathon artifact is not modified by packaging.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import textwrap
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "submission" / "REPORT.md"
DEFAULT_OUT = ROOT / "output" / "pdf" / "oracles-submission-onepager.pdf"


def _read_report() -> str:
    return REPORT.read_text(encoding="utf-8")


def _clean_md(value: str) -> str:
    value = value.replace("**", "").replace("*", "")
    value = value.replace(chr(8595), "")
    value = value.replace(chr(8212), "-").replace(chr(8211), "-")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _model_rows(report: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    in_table = False
    for line in report.splitlines():
        if line.startswith("| Variant | Single-binary"):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("|---"):
            continue
        if not line.startswith("|"):
            break
        cells = [_clean_md(c) for c in line.strip("|").split("|")]
        if len(cells) >= 3:
            rows.append((cells[0], cells[1], cells[2]))
    return rows[:6]


def _extract_ci(report: str) -> str:
    match = re.search(r"paired-bootstrap CI of \[([^\]]+)\]", report)
    return f"[{match.group(1)}]" if match else "[0.0143, 0.0374]"


def _extract_decomposition(report: str) -> str:
    match = re.search(r"Roughly \*\*(\d+%) of the Phase 2 improvement", report)
    return match.group(1) if match else "85%"


def _short_variant_name(name: str) -> str:
    replacements = {
        "Claude Opus 4.7 (production)": "Claude Opus 4.7 (prod)",
        "Claude Opus 4.6 (PA leaderboard top agent)": "Claude Opus 4.6",
        "Claude Sonnet 4.6 (previous prod)": "Claude Sonnet 4.6",
        "Gemini 3.1 Pro Preview": "Gemini 3.1 Pro",
    }
    return replacements.get(name, name)


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--short=8", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "dev"


def _draw_wrapped(
    fig: plt.Figure,
    x: float,
    y: float,
    text: str,
    *,
    width: int,
    size: float = 8.5,
    color: str = "#374151",
    weight: str = "normal",
    line_height: float = 0.018,
) -> float:
    for line in textwrap.wrap(text, width=width):
        fig.text(x, y, line, fontsize=size, color=color, weight=weight, family="DejaVu Sans")
        y -= line_height
    return y


def build_pdf(out_path: Path) -> Path:
    report = _read_report()
    rows = _model_rows(report)
    ci = _extract_ci(report)
    decomposition = _extract_decomposition(report)
    commit = _git_commit()

    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(8.5, 11), dpi=160)
    fig.patch.set_facecolor("#f7f8fb")
    canvas = fig.add_axes([0, 0, 1, 1])
    canvas.axis("off")

    # Header
    fig.text(0.07, 0.94, "The Oracles", fontsize=25, weight="bold", color="#111827")
    fig.text(0.07, 0.915, "ForecastingPath probabilistic forecasting endpoint", fontsize=11, color="#4b5563")
    fig.text(0.07, 0.892, "Team CanadaHacks - Prophet Hacks 2026", fontsize=8.5, color="#667085")
    fig.text(0.78, 0.94, f"commit {commit}", fontsize=8.5, color="#4b5563", ha="right")

    # Metric block
    fig.add_artist(Rectangle((0.07, 0.785), 0.26, 0.085, facecolor="#ffffff", edgecolor="#d8dde6", linewidth=0.8))
    fig.text(0.09, 0.842, "Single-binary Brier", fontsize=8, color="#667085", weight="bold")
    fig.text(0.09, 0.804, "0.0378", fontsize=25, color="#111827", weight="bold")
    fig.text(0.09, 0.793, "lower is better", fontsize=7.8, color="#667085")

    fig.add_artist(Rectangle((0.37, 0.785), 0.26, 0.085, facecolor="#ffffff", edgecolor="#d8dde6", linewidth=0.8))
    fig.text(0.39, 0.842, "95% paired bootstrap CI", fontsize=8, color="#667085", weight="bold")
    fig.text(0.39, 0.813, ci, fontsize=14.5, color="#111827", weight="bold")
    fig.text(0.39, 0.795, "Opus 4.7 vs Sonnet 4.6, n=26", fontsize=7.8, color="#667085")

    fig.add_artist(Rectangle((0.67, 0.785), 0.26, 0.085, facecolor="#ffffff", edgecolor="#d8dde6", linewidth=0.8))
    fig.text(0.69, 0.842, "Primary discipline finding", fontsize=8, color="#667085", weight="bold")
    fig.text(0.69, 0.813, f"{decomposition} floor fix", fontsize=15, color="#111827", weight="bold")
    fig.text(0.69, 0.795, "safety-net bug > model swap", fontsize=7.8, color="#667085")

    # Left narrative
    fig.text(0.07, 0.735, "What runs live", fontsize=12, weight="bold", color="#111827")
    y = 0.708
    bullets = [
        "FastAPI endpoint on Railway: POST /predict returns per-outcome probabilities.",
        "Pipeline: event JSON, Brave evidence, source ranking, Opus 4.7 forecast, Kalshi longshot floor, structured JSON.",
        "Every prediction writes a private trace: query, evidence URLs, raw model output, parser path, latency, and warnings.",
        "Public root stays sparse; dashboard, reports, galleries, and traces are auth-gated during active scoring.",
    ]
    for bullet in bullets:
        fig.text(0.078, y, "-", fontsize=8.5, color="#1d4ed8")
        y = _draw_wrapped(fig, 0.093, y, bullet, width=54, size=8.4, line_height=0.016)
        y -= 0.008

    y -= 0.004
    fig.text(0.07, y, "Current caveats", fontsize=12, weight="bold", color="#111827")
    y -= 0.027
    caveats = [
        "Resolved-set retrieval leaks future information on some rows; absolute backtest Brier is best-case-with-hindsight.",
        "PA CLI single-binary and proper multi-class Brier rank variants differently; claims must name the metric.",
        "First live Prophet Arena call is still the trigger for payload-shape, latency, and scoring validation.",
    ]
    for bullet in caveats:
        fig.text(0.078, y, "-", fontsize=8.5, color="#b45309")
        y = _draw_wrapped(fig, 0.093, y, bullet, width=54, size=8.4, line_height=0.016)
        y -= 0.008

    # Right table
    fig.text(0.53, 0.735, "Measured variants", fontsize=12, weight="bold", color="#111827")
    fig.text(0.53, 0.716, "Same retrieval and prompt; only model call changes.", fontsize=7.8, color="#667085")
    table_ax = fig.add_axes([0.53, 0.462, 0.40, 0.235])
    table_ax.axis("off")
    table_rows = [[_short_variant_name(name), binary, multi] for name, binary, multi in rows]
    table = table_ax.table(
        cellText=table_rows,
        colLabels=["Variant", "Binary", "Multi"],
        colWidths=[0.58, 0.20, 0.22],
        cellLoc="left",
        loc="upper left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(6.8)
    table.scale(1.0, 1.35)
    for (r, _c), cell in table.get_celld().items():
        cell.set_edgecolor("#d8dde6")
        cell.set_linewidth(0.5)
        if r == 0:
            cell.set_text_props(weight="bold", color="#374151")
            cell.set_facecolor("#eef2f7")
        elif r == 1:
            cell.set_facecolor("#ecfdf5")
        else:
            cell.set_facecolor("#ffffff")

    fig.text(0.53, 0.405, "Limitations", fontsize=12, weight="bold", color="#111827")
    y = 0.378
    for bullet in [
        "Live performance is unverified. Prophet Arena scoring starts after submission.",
        "The 0.0378 backtest used resolved events; 38.5% of evidence URLs contain post-resolution markers. Live numbers will likely be higher.",
        "Sample is small (n=26) and 62% sports. A balanced eval would widen the picture.",
    ]:
        fig.text(0.538, y, "-", fontsize=8.5, color="#b45309")
        y = _draw_wrapped(fig, 0.553, y, bullet, width=44, size=8.3, line_height=0.016)
        y -= 0.008

    # Footer
    fig.add_artist(Rectangle((0.07, 0.085), 0.86, 0.07, facecolor="#111827", edgecolor="#111827"))
    fig.text(0.09, 0.126, "Demo path", fontsize=8.5, color="#93c5fd", weight="bold")
    fig.text(
        0.09,
        0.105,
        "Public root -> dashboard -> observatory -> pipeline trace -> abstain slider -> heatmap -> review brief",
        fontsize=8.1,
        color="#e5e7eb",
    )
    fig.text(0.07, 0.052, "Generated from submission/REPORT.md by scripts/build_submission_onepager.py", fontsize=7, color="#667085")

    fig.savefig(out_path, format="pdf", facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    path = build_pdf(args.output)
    print(path)


if __name__ == "__main__":
    main()
