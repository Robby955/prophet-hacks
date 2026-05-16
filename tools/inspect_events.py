#!/usr/bin/env python3
"""Inspect a events.json / tasks.jsonl / release dir and print a summary.

Dead-simple shape reporter. Use it FIRST when a new dataset drops to
confirm the schema before running anything that depends on it.

Usage:
    python tools/inspect_events.py events.json
    python tools/inspect_events.py datasets/hackathon-day/releases/2026-05-12/
    python tools/inspect_events.py path/to/jsonl-shard-dir/

Output is intentionally plain text — easy to paste into a status update
or eyeball during the live window.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forecasting.dataset_loader import load  # noqa: E402
from forecasting.schema import ForecastTask  # noqa: E402


def summarize(tasks):
    n = len(tasks)
    binary = sum(1 for t in tasks if t.is_binary)
    multi = n - binary
    resolved = sum(1 for t in tasks if t.is_resolved)

    # Field-presence counts
    fields = Counter()
    for t in tasks:
        fields["task_id"] += 1
        fields["title"] += 1
        fields["outcomes"] += 1
        if t.context:
            fields["context"] += 1
        if t.source:
            fields["source"] += 1
        if t.metadata:
            for k in t.metadata:
                fields[f"metadata.{k}"] += 1

    # Category distribution
    categories = Counter(t.category for t in tasks)

    # Schema-version distribution
    schemas = Counter(t.schema_version_seen or "unknown" for t in tasks)

    # Outcome cardinality
    cardinalities = Counter(len(t.outcomes) for t in tasks)

    return {
        "n": n,
        "binary": binary,
        "multi_outcome": multi,
        "resolved": resolved,
        "unresolved": n - resolved,
        "fields": fields.most_common(),
        "categories": categories.most_common(),
        "schemas": schemas.most_common(),
        "outcome_cardinalities": cardinalities.most_common(),
    }


def print_report(path: Path, summary: dict, release_info: dict, errors: list) -> None:
    print(f"file: {path}")
    if release_info:
        rid = release_info.get("release_id") or release_info.get("id")
        ds = release_info.get("dataset") or release_info.get("dataset_id")
        if rid or ds:
            print(f"release: dataset={ds} release_id={rid}")
    print(f"tasks: {summary['n']}")
    print(f"  binary: {summary['binary']}")
    print(f"  multi-outcome: {summary['multi_outcome']}")
    print(f"  resolved: {summary['resolved']}")
    print(f"  unresolved: {summary['unresolved']}")

    print()
    print("fields observed:")
    for name, count in summary["fields"]:
        print(f"  {name}: {count}")

    print()
    print("schema versions:")
    for ver, count in summary["schemas"]:
        print(f"  {ver}: {count}")

    print()
    print("outcome cardinalities:")
    for k, count in summary["outcome_cardinalities"]:
        print(f"  {k}-outcome: {count}")

    print()
    print("categories:")
    for cat, count in summary["categories"]:
        print(f"  {cat}: {count}")

    if errors:
        print()
        print(f"WARN: {len(errors)} normalization error(s)")
        for src, msg in errors[:10]:
            print(f"  {src}: {msg}")
        if len(errors) > 10:
            print(f"  ... ({len(errors) - 10} more)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON summary instead of text.")
    ap.add_argument("--strict", action="store_true",
                    help="Fail fast on any normalization error.")
    args = ap.parse_args()

    result = load(args.path, strict=args.strict)
    summary = summarize(result.tasks)

    if args.json:
        print(json.dumps({
            "summary": summary,
            "release": result.release_info,
            "errors": result.errors,
            "source_path": str(result.source_path) if result.source_path else None,
        }, indent=2, default=str))
    else:
        print_report(args.path, summary, result.release_info, result.errors)

    return 0 if not result.errors else (1 if args.strict else 0)


if __name__ == "__main__":
    raise SystemExit(main())
