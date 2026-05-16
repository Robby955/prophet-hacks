#!/usr/bin/env python3
"""Convert a resolved-events JSON file into the actuals format the evaluator expects.

Input (one element from `prophet forecast retrieve --dataset sample-resolved
--include-resolved`):

    {
        "market_ticker": "KXITFWMATCH-26MAY12NAJEBS",
        "outcomes": ["Kaja Najzer", "Anna Lena Ebster"],
        "resolved_outcome": {"value": ["Anna Lena Ebster"], ...},
        ...
    }

Output (the actuals.json shape expected by `prophet forecast evaluate`):

    {"KXITFWMATCH-26MAY12NAJEBS": 0.0, ...}

The binary YES condition is `resolved_outcome.value == [outcomes[0]]`. A
1.0 means outcomes[0] won; 0.0 means anything else did.

Usage:
    python scripts/build_actuals.py data/resolved.json data/actuals.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def to_binary(event: dict) -> float | None:
    """Return 1.0 if outcomes[0] is the resolved winner, 0.0 otherwise.

    Returns None if the event has no outcomes or no resolved_outcome.
    """
    outs = event.get("outcomes") or []
    if not outs:
        return None
    res = event.get("resolved_outcome") or {}
    val = res.get("value")
    if val is None:
        return None
    # value is a list (e.g. ["Kevin Hart"]); compare to outcomes[0].
    if isinstance(val, list):
        return 1.0 if val == [outs[0]] else 0.0
    # Defensive: if value is a string for some events, compare directly.
    return 1.0 if val == outs[0] else 0.0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <resolved.json> <out actuals.json>", file=sys.stderr)
        return 2
    events = json.loads(Path(argv[1]).read_text())
    actuals: dict[str, float] = {}
    skipped = 0
    for e in events:
        ticker = e.get("market_ticker") or e.get("event_ticker")
        if not ticker:
            skipped += 1
            continue
        b = to_binary(e)
        if b is None:
            skipped += 1
            continue
        actuals[ticker] = b
    Path(argv[2]).write_text(json.dumps(actuals, indent=2, sort_keys=True))
    yes_count = sum(1 for v in actuals.values() if v == 1.0)
    print(
        f"wrote {len(actuals)} actuals "
        f"({yes_count} YES, {len(actuals) - yes_count} NO; "
        f"{skipped} skipped) to {argv[2]}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
