#!/usr/bin/env python3
"""Run forward shadow forecasts without touching Prophet Arena scoring.

This script is for live calibration while PA's official evaluator is quiet:
keep a small queue of real upcoming events, call our deployed /predict endpoint,
and append timestamped records to an ignored JSONL log. Later, when outcomes
resolve, those records can be scored against both our forecast and the market
snapshot captured at prediction time.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


DEFAULT_EVENTS = Path("data/shadow_calibration/events.json")
DEFAULT_OUT = Path("logs/shadow_calibration.jsonl")
DEFAULT_ENDPOINT = "https://agent.forecastingpath.com/predict"


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(raw: str) -> datetime:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError(f"datetime must include timezone: {raw}")
    return dt.astimezone(UTC)


def _load_events(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON list")
    return payload


def _event_id(event: dict[str, Any]) -> str:
    return str(event.get("event_ticker") or event.get("market_ticker") or event.get("id") or "")


def _validate_event(event: dict[str, Any]) -> None:
    event_id = _event_id(event)
    if not event_id:
        raise ValueError("event missing event_ticker/market_ticker/id")
    for key in ("title", "outcomes", "close_time"):
        if key not in event:
            raise ValueError(f"{event_id}: missing {key}")
    outcomes = event["outcomes"]
    if not isinstance(outcomes, list) or len(outcomes) < 2:
        raise ValueError(f"{event_id}: outcomes must be a list with at least two entries")
    if not all(isinstance(x, str) and x.strip() for x in outcomes):
        raise ValueError(f"{event_id}: outcomes must be non-empty strings")
    _parse_dt(str(event["close_time"]))


def _existing_event_ids(out: Path) -> set[str]:
    if not out.exists():
        return set()
    seen: set[str] = set()
    for line in out.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_id = str(record.get("event_ticker") or record.get("market_ticker") or "")
        if event_id:
            seen.add(event_id)
    return seen


def _endpoint_payload(event: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "event_ticker",
        "market_ticker",
        "title",
        "description",
        "category",
        "close_time",
        "outcomes",
        "rules",
    }
    return {k: v for k, v in event.items() if k in allowed}


def _probability_sum(response: dict[str, Any]) -> float | None:
    probs = response.get("probabilities")
    if not isinstance(probs, list):
        return None
    try:
        return round(sum(float(p.get("probability", 0.0)) for p in probs), 6)
    except (TypeError, ValueError):
        return None


def run_shadow_forecasts(
    *,
    events_path: Path,
    endpoint: str,
    out: Path,
    limit: int | None,
    force: bool,
    include_closed: bool,
    dry_run: bool,
    timeout: float,
) -> list[dict[str, Any]]:
    events = _load_events(events_path)
    for event in events:
        _validate_event(event)

    out.parent.mkdir(parents=True, exist_ok=True)
    seen = _existing_event_ids(out)
    now = _now()
    records: list[dict[str, Any]] = []
    attempted = 0

    for event in events:
        event_id = _event_id(event)
        closes_at = _parse_dt(str(event["close_time"]))
        if not force and event_id in seen:
            print(f"skip existing {event_id}")
            continue
        if not include_closed and closes_at <= now:
            print(f"skip closed {event_id} close_time={event['close_time']}")
            continue
        if limit is not None and attempted >= limit:
            break

        attempted += 1
        payload = _endpoint_payload(event)
        record: dict[str, Any] = {
            "recorded_at": _now().isoformat().replace("+00:00", "Z"),
            "event_ticker": event_id,
            "market_ticker": str(event.get("market_ticker") or event_id),
            "title": event["title"],
            "category": event.get("category"),
            "close_time": event["close_time"],
            "outcomes": event["outcomes"],
            "endpoint": endpoint,
            "market_snapshot": event.get("market_snapshot", {}),
            "notes": event.get("notes", ""),
            "status": "dry_run" if dry_run else "pending",
        }
        if dry_run:
            record["request"] = payload
            records.append(record)
            print(f"dry-run {event_id}")
            continue

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(endpoint, json=payload)
            record["http_status"] = resp.status_code
            if resp.status_code >= 400:
                record["status"] = "http_error"
                record["error"] = resp.text[:1000]
            else:
                body = resp.json()
                record["status"] = "ok"
                record["response"] = body
                record["probability_sum"] = _probability_sum(body)
        except Exception as exc:  # pragma: no cover - defensive CLI boundary
            record["status"] = "exception"
            record["error"] = repr(exc)

        with out.open("a") as fh:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        records.append(record)
        print(f"{record['status']} {event_id}")

    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true", help="forecast events already present in the output log")
    parser.add_argument("--include-closed", action="store_true", help="allow events whose close_time is already past")
    parser.add_argument("--dry-run", action="store_true", help="validate and print without calling /predict")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    try:
        records = run_shadow_forecasts(
            events_path=args.events,
            endpoint=args.endpoint,
            out=args.out,
            limit=args.limit,
            force=args.force,
            include_closed=args.include_closed,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"shadow calibration failed: {exc}", file=sys.stderr)
        return 1
    print(f"records={len(records)} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
