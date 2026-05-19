#!/usr/bin/env python3
"""Render a local HTML dashboard for forward shadow calibration.

The dashboard reads:
  - data/shadow_calibration/events.json
  - logs/shadow_calibration.jsonl
  - data/shadow_calibration/resolutions.json

It writes logs/shadow_calibration.html by default. The output is local and
git-ignored; the script is committed so the view can be regenerated as new
forecasts or resolutions land.
"""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_EVENTS = Path("data/shadow_calibration/events.json")
DEFAULT_LOG = Path("logs/shadow_calibration.jsonl")
DEFAULT_RESOLUTIONS = Path("data/shadow_calibration/resolutions.json")
DEFAULT_OUT = Path("logs/shadow_calibration.html")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _event_id(row: dict[str, Any]) -> str:
    return str(row.get("event_ticker") or row.get("market_ticker") or "")


def _winner(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("winner") or "")
    return ""


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        text = str(raw)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def _fmt_dt(raw: Any) -> str:
    dt = _parse_dt(raw)
    if not dt:
        return "unknown"
    return dt.strftime("%b %-d, %H:%M UTC")


def _hours_until(raw: Any, now: datetime) -> str:
    dt = _parse_dt(raw)
    if not dt:
        return "unknown"
    hours = (dt - now).total_seconds() / 3600
    if hours < -1:
        return f"{abs(hours):.1f}h ago"
    if hours < 0:
        return "closing now"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _probabilities(record: dict[str, Any]) -> list[dict[str, Any]]:
    response = record.get("response")
    if not isinstance(response, dict):
        return []
    probs = response.get("probabilities")
    if not isinstance(probs, list):
        return []
    return [p for p in probs if isinstance(p, dict)]


def _probability_cells(record: dict[str, Any], winner: str) -> str:
    probs = _probabilities(record)
    if not probs:
        return '<span class="muted">no forecast</span>'
    parts = []
    for p in probs:
        market = html.escape(str(p.get("market") or ""))
        try:
            probability = float(p.get("probability", 0.0))
        except (TypeError, ValueError):
            probability = 0.0
        cls = "prob winner" if winner and market == html.escape(winner) else "prob"
        parts.append(f'<span class="{cls}"><b>{probability:.3f}</b> {market}</span>')
    return "".join(parts)


def _brier(record: dict[str, Any], winner: str) -> str:
    if not winner:
        return '<span class="muted">pending</span>'
    probs = _probabilities(record)
    by_market = {}
    for p in probs:
        try:
            by_market[str(p.get("market") or "")] = float(p.get("probability", 0.0))
        except (TypeError, ValueError):
            pass
    if winner not in by_market:
        return '<span class="warn">winner missing</span>'
    winner_p = by_market[winner]
    winner_brier = (1.0 - winner_p) ** 2
    multi = sum((p - (1.0 if m == winner else 0.0)) ** 2 for m, p in by_market.items())
    return f"<b>{winner_brier:.4f}</b><span class=\"muted\"> / {multi:.4f}</span>"


def _source_links(event: dict[str, Any]) -> str:
    snap = event.get("market_snapshot")
    if not isinstance(snap, dict):
        return ""
    urls = snap.get("source_urls")
    if not isinstance(urls, list):
        return ""
    links = []
    for i, url in enumerate(urls[:3], start=1):
        safe = html.escape(str(url), quote=True)
        links.append(f'<a href="{safe}">source {i}</a>')
    return " ".join(links)


def render_dashboard(
    *,
    events_path: Path = DEFAULT_EVENTS,
    log_path: Path = DEFAULT_LOG,
    resolutions_path: Path = DEFAULT_RESOLUTIONS,
) -> str:
    events = _load_json(events_path, [])
    if not isinstance(events, list):
        events = []
    logs = _load_jsonl(log_path)
    resolutions_raw = _load_json(resolutions_path, {})
    if not isinstance(resolutions_raw, dict):
        resolutions_raw = {}
    resolutions = {str(k): _winner(v) for k, v in resolutions_raw.items()}

    latest_by_id: dict[str, dict[str, Any]] = {}
    for row in logs:
        event_id = _event_id(row)
        if event_id:
            latest_by_id[event_id] = row

    now = datetime.now(UTC)
    logged = len(latest_by_id)
    resolved = sum(1 for event_id in latest_by_id if resolutions.get(event_id))
    pending = max(0, logged - resolved)
    categories = Counter(str(r.get("category") or "unknown") for r in logs)

    rows = []
    for event in sorted(events, key=lambda e: str(e.get("close_time") or "")):
        event_id = _event_id(event)
        record = latest_by_id.get(event_id, {})
        winner = resolutions.get(event_id, "")
        has_record = bool(record)
        status = "scored" if has_record and winner else "logged" if has_record else "queued"
        if winner and not has_record:
            status = "resolved-no-forecast"
        status_label = status.replace("-", " ")
        title = html.escape(str(event.get("title") or record.get("title") or event_id))
        rationale = html.escape(str((record.get("response") or {}).get("rationale") or ""))
        rows.append(
            f"""
            <tr>
              <td><span class="status {status}">{status_label}</span></td>
              <td>
                <div class="title">{title}</div>
                <div class="meta">{html.escape(event_id)} · closes {_fmt_dt(event.get("close_time"))} · {_hours_until(event.get("close_time"), now)}</div>
                <div class="sources">{_source_links(event)}</div>
              </td>
              <td>{_probability_cells(record, winner)}</td>
              <td>{html.escape(winner) if winner else '<span class="muted">pending</span>'}</td>
              <td>{_brier(record, winner)}</td>
              <td><span class="rationale">{rationale}</span></td>
            </tr>
            """
        )

    category_bits = " ".join(
        f'<span class="chip">{html.escape(cat)} {count}</span>'
        for cat, count in sorted(categories.items())
    ) or '<span class="chip">none</span>'

    generated = now.strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Shadow Calibration</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f3;
      --ink: #171713;
      --muted: #6d6b62;
      --line: #d9d6c9;
      --accent: #0f766e;
      --panel: #fffef9;
      --warn: #a16207;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ width: min(1440px, calc(100vw - 48px)); margin: 0 auto; padding: 34px 0 48px; }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 24px;
      align-items: end;
      padding-bottom: 22px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0; font-size: clamp(28px, 4vw, 56px); line-height: .95; letter-spacing: 0; }}
    .sub {{ color: var(--muted); max-width: 720px; margin-top: 12px; }}
    .stats {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .stat {{ min-width: 112px; background: var(--panel); border: 1px solid var(--line); padding: 12px 14px; }}
    .stat b {{ display: block; font-size: 26px; line-height: 1; }}
    .stat span, .meta, .muted, .rationale {{ color: var(--muted); }}
    .toolbar {{ display: flex; justify-content: space-between; align-items: center; gap: 20px; padding: 18px 0; }}
    .chip {{ display: inline-block; border: 1px solid var(--line); padding: 5px 9px; background: var(--panel); margin: 0 5px 5px 0; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); }}
    th, td {{ text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); padding: 13px 12px; }}
    th {{ font-size: 12px; text-transform: uppercase; color: var(--muted); letter-spacing: .04em; background: #f0eee5; }}
    tr:last-child td {{ border-bottom: 0; }}
    .title {{ font-weight: 650; max-width: 560px; }}
    .status {{ display: inline-block; min-width: 112px; padding: 5px 8px; border: 1px solid var(--line); text-align: center; background: #f5f3ea; }}
    .status.logged {{ color: var(--accent); border-color: #99c9c0; background: #e8f4f1; }}
    .status.scored {{ color: #166534; border-color: #9bc59d; background: #edf7ed; }}
    .status.resolved-no-forecast {{ color: var(--warn); border-color: #d8b35c; background: #fbf2d6; }}
    .prob {{ display: block; margin: 0 0 5px; white-space: nowrap; }}
    .prob b {{ font-variant-numeric: tabular-nums; }}
    .prob.winner b {{ color: #166534; }}
    .sources a {{ color: var(--accent); margin-right: 8px; text-decoration: none; }}
    .warn {{ color: var(--warn); }}
    code {{ background: #ece8da; padding: 2px 5px; }}
    @media (max-width: 900px) {{
      main {{ width: min(100vw - 24px, 900px); padding-top: 20px; }}
      header {{ grid-template-columns: 1fr; }}
      .stats {{ justify-content: flex-start; }}
      table {{ display: block; overflow-x: auto; white-space: normal; }}
      th, td {{ min-width: 140px; }}
      td:nth-child(2) {{ min-width: 320px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Shadow Calibration</h1>
        <div class="sub">Forward forecasts on real events before resolution. This view is for calibration and model comparison; it does not change the production endpoint.</div>
      </div>
      <div class="stats">
        <div class="stat"><b>{len(events)}</b><span>queued</span></div>
        <div class="stat"><b>{logged}</b><span>logged</span></div>
        <div class="stat"><b>{resolved}</b><span>scored</span></div>
        <div class="stat"><b>{pending}</b><span>pending</span></div>
      </div>
    </header>
    <div class="toolbar">
      <div>{category_bits}</div>
      <div class="muted">Generated {generated}. Refresh with <code>.venv/bin/python scripts/render_shadow_dashboard.py</code></div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Status</th>
          <th>Event</th>
          <th>Forecast</th>
          <th>Winner</th>
          <th>Brier</th>
          <th>Rationale</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--resolutions", type=Path, default=DEFAULT_RESOLUTIONS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    html_out = render_dashboard(
        events_path=args.events,
        log_path=args.log,
        resolutions_path=args.resolutions,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_out)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
