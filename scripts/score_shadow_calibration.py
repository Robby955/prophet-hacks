#!/usr/bin/env python3
"""Score resolved shadow forecasts from logs/shadow_calibration.jsonl.

The resolution file is intentionally small and manual:

{
  "SHADOW-MLB-ATL-MIA-20260519": "Atlanta Braves",
  "SHADOW-NBA-CLE-NYK-G1-20260519": {
    "winner": "New York Knicks",
    "resolved_at": "2026-05-20T02:30:00Z"
  }
}

Only rows with a matching resolution are scored. This keeps the forward
calibration loop simple: add events before start, forecast once, fill winners
after final scores, then run this scorer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


DEFAULT_LOG = Path("logs/shadow_calibration.jsonl")
DEFAULT_RESOLUTIONS = Path("data/shadow_calibration/resolutions.json")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSONL row") from exc
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _load_resolutions(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    out: dict[str, str] = {}
    for event_id, value in payload.items():
        if isinstance(value, str):
            winner = value
        elif isinstance(value, dict) and isinstance(value.get("winner"), str):
            winner = value["winner"]
        else:
            raise ValueError(f"{path}: {event_id} must map to a winner string or object with winner")
        winner = winner.strip()
        if winner:
            out[str(event_id)] = winner
    return out


def _probabilities(record: dict[str, Any]) -> list[dict[str, Any]]:
    response = record.get("response")
    if not isinstance(response, dict):
        return []
    probs = response.get("probabilities")
    if not isinstance(probs, list):
        return []
    return [p for p in probs if isinstance(p, dict)]


def score_records(
    records: list[dict[str, Any]],
    resolutions: dict[str, str],
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for record in records:
        event_id = str(record.get("event_ticker") or record.get("market_ticker") or "")
        if not event_id or event_id not in resolutions:
            continue
        winner = resolutions[event_id]
        probs = _probabilities(record)
        by_market: dict[str, float] = {}
        for item in probs:
            market = str(item.get("market") or "")
            if not market:
                continue
            try:
                by_market[market] = float(item.get("probability", 0.0))
            except (TypeError, ValueError):
                by_market[market] = 0.0
        if winner not in by_market:
            scored.append(
                {
                    "event_ticker": event_id,
                    "status": "missing_winner_probability",
                    "winner": winner,
                    "markets": sorted(by_market),
                }
            )
            continue
        multiclass_brier = 0.0
        for market, probability in by_market.items():
            actual = 1.0 if market == winner else 0.0
            multiclass_brier += (probability - actual) ** 2
        winner_probability = by_market[winner]
        winner_brier = (1.0 - winner_probability) ** 2
        scored.append(
            {
                "event_ticker": event_id,
                "status": "scored",
                "title": record.get("title"),
                "winner": winner,
                "winner_probability": round(winner_probability, 6),
                "winner_brier": round(winner_brier, 6),
                "multiclass_brier": round(multiclass_brier, 6),
                "probability_sum": record.get("probability_sum"),
                "recorded_at": record.get("recorded_at"),
                "close_time": record.get("close_time"),
            }
        )
    return scored


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--resolutions", type=Path, default=DEFAULT_RESOLUTIONS)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        records = _load_jsonl(args.log)
        resolutions = _load_resolutions(args.resolutions)
        scored = score_records(records, resolutions)
    except Exception as exc:
        print(f"shadow scoring failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(scored, indent=2, sort_keys=True))
        return 0

    good = [row for row in scored if row.get("status") == "scored"]
    for row in scored:
        if row.get("status") != "scored":
            print(f"{row['event_ticker']}\t{row['status']}\twinner={row['winner']}")
            continue
        print(
            "\t".join(
                [
                    str(row["event_ticker"]),
                    f"winner={row['winner']}",
                    f"p={row['winner_probability']:.3f}",
                    f"winner_brier={row['winner_brier']:.4f}",
                    f"multiclass_brier={row['multiclass_brier']:.4f}",
                ]
            )
        )
    mean_winner = _mean([float(row["winner_brier"]) for row in good])
    mean_multi = _mean([float(row["multiclass_brier"]) for row in good])
    if mean_winner is None:
        print("summary\tscored=0\tmissing=0")
    else:
        print(
            "summary\t"
            f"scored={len(good)}\t"
            f"missing={len(scored) - len(good)}\t"
            f"mean_winner_brier={mean_winner:.4f}"
        )
    if mean_multi is not None:
        print(f"summary\tmean_multiclass_brier={mean_multi:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
