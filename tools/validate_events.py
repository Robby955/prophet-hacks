#!/usr/bin/env python3
"""Strict validator — fail fast on any bad event before we waste a tick on it.

Run before any production forecast pass. Exits non-zero on:
- missing task_id / title / outcomes
- empty outcomes list
- duplicate outcomes
- resolved_outcome value not in declared outcomes
- bad ISO timestamps in metadata or resolved_outcome
- unknown schema shape that the loader couldn't normalize

This is intentionally MORE strict than the loader. The loader is
tolerant for inspection; this validator is the gate before scoring.

Usage:
    python tools/validate_events.py events.json
    python tools/validate_events.py datasets/hackathon-day/releases/2026-05-12/
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forecasting.dataset_loader import load  # noqa: E402
from forecasting.schema import ForecastTask  # noqa: E402


def _is_valid_iso(s: str) -> bool:
    if not isinstance(s, str) or not s:
        return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_task(t: ForecastTask) -> List[str]:
    issues: List[str] = []

    if not t.task_id:
        issues.append("empty task_id")
    if not t.title:
        issues.append("empty title")
    if not t.outcomes:
        issues.append("empty outcomes")
    elif len(t.outcomes) < 2:
        issues.append(f"outcomes has {len(t.outcomes)} entries; need >= 2")

    # Duplicate outcomes (normalize.py dedupes, so this should never fire — defensive)
    if len(t.outcomes) != len(set(t.outcomes)):
        issues.append("duplicate outcomes")

    if t.is_binary and t.yes_label is None:
        issues.append("binary task with no yes_label inferred")

    # ISO timestamp checks
    ct = t.close_time
    if ct is not None and not _is_valid_iso(ct):
        issues.append(f"bad close_time/resolution_time: {ct!r}")

    if t.resolved is not None and t.resolved.resolved_at:
        if not _is_valid_iso(t.resolved.resolved_at):
            issues.append(f"bad resolved_at: {t.resolved.resolved_at!r}")

    # Resolved value must be subset of outcomes (normalize.py also checks; defensive)
    if t.resolved is not None:
        for v in t.resolved.value:
            if v not in t.outcomes:
                issues.append(f"resolved value {v!r} not in outcomes {t.outcomes}")

    return issues


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--allow-warnings", action="store_true",
                    help="Exit 0 even if validation issues found. Default: fail.")
    args = ap.parse_args()

    # Use the loader in non-strict mode so we get a list of all problems
    result = load(args.path, strict=False)

    all_issues: List[Tuple[str, List[str]]] = []
    for t in result.tasks:
        issues = validate_task(t)
        if issues:
            all_issues.append((t.task_id, issues))

    print(f"validated {len(result.tasks)} tasks from {result.source_path}")
    if result.errors:
        print(f"loader-level errors: {len(result.errors)}")
        for src, msg in result.errors[:10]:
            print(f"  {src}: {msg}")
        if len(result.errors) > 10:
            print(f"  ... ({len(result.errors) - 10} more)")
    print(f"per-task issues: {sum(len(i) for _, i in all_issues)} across {len(all_issues)} tasks")
    for tid, issues in all_issues[:20]:
        for msg in issues:
            print(f"  {tid}: {msg}")
    if len(all_issues) > 20:
        print(f"  ... ({len(all_issues) - 20} more tasks with issues)")

    fail = bool(result.errors or all_issues)
    if fail and not args.allow_warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
