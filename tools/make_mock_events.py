#!/usr/bin/env python3
"""Generate synthetic event datasets matching the ai-prophet-datasets schema.

Used to:
- Seed offline pastcast harness with controlled distributions
- Generate fixtures that exercise specific failure modes
  (longshots, favorites, multi-outcome, bad-schema)
- Provide reproducible smoke-test inputs for CI

Usage:
    python tools/make_mock_events.py --count 50 --domain sports \\
        --resolved-fraction 1.0 -o data/fixtures/sports_resolved.jsonl

    python tools/make_mock_events.py --shape multi-outcome --count 10 \\
        -o data/fixtures/multi_outcome.jsonl

The output is line-oriented JSONL by default. Pass --json to emit a
single events.json wrapper list instead.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List


DOMAINS = (
    "sports", "finance", "crypto", "weather",
    "elections", "science", "tech", "geopolitics",
    "health", "other",
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def make_binary_task(
    rng: random.Random,
    *,
    idx: int,
    domain: str,
    resolved: bool,
    p_market_bucket: str = "any",
) -> dict:
    """Produce one binary task in the dataset-v1 shape."""
    base = datetime(2026, 5, 15, tzinfo=timezone.utc)
    horizon_hours = rng.choice([4, 12, 24, 48, 24 * 7, 24 * 30])
    close = base + timedelta(hours=horizon_hours)

    # Skew market-implied probability by bucket
    if p_market_bucket == "longshot":
        p_market = rng.uniform(0.02, 0.10)
    elif p_market_bucket == "favorite":
        p_market = rng.uniform(0.85, 0.97)
    else:
        p_market = rng.uniform(0.10, 0.90)

    # Outcome resolves with probability ~= p_market (calibrated mock)
    outcome_yes = rng.random() < p_market

    task_id = f"mock-{domain}-{idx:04d}"
    task: dict = {
        "task_id": task_id,
        "title": _make_title(domain, idx),
        "outcomes": ["Yes", "No"],
        "source": "mock",
        "metadata": {
            "market_ticker": task_id,
            "close_time": _iso(close),
            "category": domain,
            "market_implied_p_yes": round(p_market, 4),
        },
    }
    if resolved:
        task["resolved_outcome"] = {
            "value": ["Yes" if outcome_yes else "No"],
            "resolved_at": _iso(close + timedelta(minutes=rng.randint(1, 60))),
            "source": task_id,
        }
    return task


def _make_title(domain: str, idx: int) -> str:
    templates = {
        "sports": f"Will team_{idx % 30} win game {idx}?",
        "finance": f"Will SPY close above {3000 + idx} this week?",
        "crypto": f"Will BTC trade above ${50_000 + idx * 100} by close?",
        "weather": f"Will city_{idx % 40} see >0.5in precipitation Thursday?",
        "elections": f"Will candidate_{idx % 10} win race {idx}?",
        "science": f"Will publication_{idx} be released this quarter?",
        "tech": f"Will Company_{idx % 20} launch product_{idx} by Q3?",
        "geopolitics": f"Will summit_{idx} produce a signed agreement?",
        "health": f"Will weekly flu positivity exceed {5 + idx % 15}%?",
        "other": f"Open-ended question {idx}?",
    }
    return templates.get(domain, f"Will event_{idx} happen?")


def make_multi_outcome_task(rng: random.Random, idx: int) -> dict:
    """Three-outcome mock for testing non-binary handling."""
    domain = rng.choice(DOMAINS)
    base = datetime(2026, 5, 15, tzinfo=timezone.utc)
    close = base + timedelta(hours=rng.choice([24, 72, 24 * 7]))
    outcomes = ["Above", "Between", "Below"]
    winner = rng.choice(outcomes)
    return {
        "task_id": f"mock-multi-{idx:04d}",
        "title": f"Where will indicator_{idx} land?",
        "outcomes": outcomes,
        "source": "mock",
        "metadata": {
            "category": domain,
            "close_time": _iso(close),
        },
        "resolved_outcome": {
            "value": [winner],
            "resolved_at": _iso(close + timedelta(minutes=10)),
            "source": f"mock-multi-{idx:04d}",
        },
    }


def make_bad_schema_task(rng: random.Random, idx: int) -> dict:
    """Intentionally malformed rows for validator stress tests."""
    bad_kind = idx % 5
    if bad_kind == 0:
        # missing task_id
        return {
            "title": "Will something happen?",
            "outcomes": ["Yes", "No"],
        }
    if bad_kind == 1:
        # empty outcomes
        return {
            "task_id": f"bad-{idx:04d}",
            "title": "Will something happen?",
            "outcomes": [],
        }
    if bad_kind == 2:
        # duplicate outcomes
        return {
            "task_id": f"bad-{idx:04d}",
            "title": "Will something happen?",
            "outcomes": ["Yes", "Yes"],
        }
    if bad_kind == 3:
        # resolved value not in outcomes
        return {
            "task_id": f"bad-{idx:04d}",
            "title": "Will something happen?",
            "outcomes": ["Yes", "No"],
            "resolved_outcome": {"value": ["Maybe"], "resolved_at": "2026-05-15T12:00:00Z"},
        }
    # bad timestamp
    return {
        "task_id": f"bad-{idx:04d}",
        "title": "Will something happen?",
        "outcomes": ["Yes", "No"],
        "metadata": {"close_time": "not-an-iso-timestamp"},
    }


# -- Main -----------------------------------------------------------------


def make_dataset(args) -> List[dict]:
    rng = random.Random(args.seed)
    tasks: List[dict] = []

    if args.shape == "binary":
        n_resolved = int(args.count * args.resolved_fraction)
        domain_iter = (
            [args.domain] * args.count if args.domain
            else [rng.choice(DOMAINS) for _ in range(args.count)]
        )
        bucket_iter = (
            [args.bucket] * args.count if args.bucket
            else [rng.choice(["any", "longshot", "favorite"]) for _ in range(args.count)]
        )
        for i, (dom, bucket) in enumerate(zip(domain_iter, bucket_iter)):
            tasks.append(make_binary_task(
                rng, idx=i, domain=dom,
                resolved=(i < n_resolved),
                p_market_bucket=bucket,
            ))
    elif args.shape == "multi-outcome":
        for i in range(args.count):
            tasks.append(make_multi_outcome_task(rng, i))
    elif args.shape == "bad-schema":
        for i in range(args.count):
            tasks.append(make_bad_schema_task(rng, i))
    else:
        raise ValueError(f"unknown shape: {args.shape}")

    return tasks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--shape", choices=("binary", "multi-outcome", "bad-schema"),
                    default="binary")
    ap.add_argument("--domain", choices=DOMAINS, default=None)
    ap.add_argument("--bucket", choices=("any", "longshot", "favorite"), default=None)
    ap.add_argument("--resolved-fraction", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--json", action="store_true",
                    help="Emit a single events.json wrapper instead of JSONL.")
    args = ap.parse_args()

    tasks = make_dataset(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    if args.json:
        args.output.write_text(json.dumps({"tasks": tasks}, indent=2))
    else:
        with args.output.open("w") as f:
            for t in tasks:
                f.write(json.dumps(t) + "\n")

    print(f"wrote {len(tasks)} tasks to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
