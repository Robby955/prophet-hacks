"""Strict no-leakage gate for offline pastcasting datasets.

A pastcast harness is worthless if the "evidence" includes information
that wouldn't have been available at forecast time. We REQUIRE every
source's published_at <= the event's forecast_time before any
variant is allowed to score against the dataset.

Call `assert_no_leakage(dataset)` in any offline evaluation runner before
scoring. Violations raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Sequence


@dataclass
class LeakageError:
    event_id: str
    source_url: str
    forecast_time: datetime
    source_published_at: datetime

    def __str__(self) -> str:
        return (
            f"LEAKAGE in event {self.event_id}: "
            f"source {self.source_url} published at {self.source_published_at.isoformat()} "
            f"is after forecast_time {self.forecast_time.isoformat()}"
        )


def parse_iso(ts) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        # Tolerate "Z" suffix
        s = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


def check_no_leakage(dataset: Iterable[dict]) -> List[LeakageError]:
    """Return a list of leakage violations. Empty list = clean dataset."""
    violations: List[LeakageError] = []
    for event in dataset:
        event_id = event.get("event_id", "<unknown>")
        forecast_time = parse_iso(event.get("forecast_time"))
        if forecast_time is None:
            continue
        for src in event.get("sources", []) or []:
            published_at = parse_iso(src.get("published_at"))
            if published_at is None:
                continue
            if published_at > forecast_time:
                violations.append(
                    LeakageError(
                        event_id=event_id,
                        source_url=src.get("url", "<no-url>"),
                        forecast_time=forecast_time,
                        source_published_at=published_at,
                    )
                )
    return violations


def assert_no_leakage(dataset: Iterable[dict]) -> None:
    """Raise if any leakage is detected. Use in scripts before scoring."""
    violations = check_no_leakage(dataset)
    if violations:
        msg = "\n".join(str(v) for v in violations)
        raise RuntimeError(f"Leakage detected ({len(violations)} violation(s)):\n{msg}")
