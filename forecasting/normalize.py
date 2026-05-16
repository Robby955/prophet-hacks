"""Normalize raw event dicts from any input shape into ForecastTask.

Supported input shapes:
- ai-prophet-datasets tasks.jsonl rows
- `prophet forecast retrieve -o events.json` (JSON list)
- JSON wrapper objects { "tasks": [...] } or { "events": [...] }
- Server-fetched event JSON (POST payloads, single-event)
- Hand-built fixtures (compat with both above)

The normalize functions DO NOT raise on missing optional fields; they
raise on missing REQUIRED fields (task_id, title, outcomes) so callers
can fail fast on bad data.

Schema versions seen so far:
- "dataset-v1" : ai-prophet-datasets tasks.jsonl (May 2026)
- "events-v1"  : `prophet forecast retrieve` events.json
- "server-v1"  : POST payload from `prophet forecast predict --agent-url`

Future organizer schema bumps land here; the forecaster never changes.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from .schema import (
    ForecastTask,
    ResolvedOutcome,
    SchemaError,
    compute_raw_hash,
    infer_yes_label,
)


# -- Generic field accessors ---------------------------------------------


def _get_str(d: Dict[str, Any], key: str) -> Optional[str]:
    v = d.get(key)
    if isinstance(v, str) and v.strip():
        return v
    return None


def _get_outcomes(d: Dict[str, Any]) -> Optional[List[str]]:
    v = d.get("outcomes") or d.get("choices") or d.get("options")
    if not isinstance(v, list):
        return None
    out = []
    for item in v:
        if isinstance(item, str) and item.strip():
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("label") or item.get("title")
            if isinstance(name, str) and name.strip():
                out.append(name)
    # Dedupe but preserve order
    seen = set()
    deduped = []
    for o in out:
        if o not in seen:
            deduped.append(o)
            seen.add(o)
    return deduped or None


def _get_resolved(d: Dict[str, Any], outcomes: List[str]) -> Optional[ResolvedOutcome]:
    raw = d.get("resolved_outcome") or d.get("resolution") or d.get("outcome")
    if raw is None:
        return None
    if isinstance(raw, str):
        value = (raw,)
        return ResolvedOutcome(value=value, resolved_at=None, source=None)
    if isinstance(raw, list):
        value = tuple(str(v) for v in raw if isinstance(v, (str, int, float)))
        return ResolvedOutcome(value=value, resolved_at=None, source=None) if value else None
    if isinstance(raw, dict):
        v = raw.get("value")
        if isinstance(v, str):
            value = (v,)
        elif isinstance(v, list):
            value = tuple(str(x) for x in v if isinstance(x, (str, int, float)))
        else:
            return None
        if not value:
            return None
        # Validate the resolved value is a subset of declared outcomes
        for vv in value:
            if vv not in outcomes:
                raise SchemaError(
                    f"resolved_outcome value {vv!r} not in declared outcomes {outcomes}",
                    raw=d,
                )
        return ResolvedOutcome(
            value=value,
            resolved_at=_get_str(raw, "resolved_at"),
            source=_get_str(raw, "source"),
        )
    return None


def _get_metadata(d: Dict[str, Any]) -> Dict[str, Any]:
    md = d.get("metadata")
    if isinstance(md, dict):
        return dict(md)
    return {}


# -- Schema detection -----------------------------------------------------


def detect_schema_version(d: Dict[str, Any]) -> str:
    """Best-effort string label for the schema we just received."""
    if "tasks" in d and isinstance(d["tasks"], list):
        return "wrapper-tasks"
    if "events" in d and isinstance(d["events"], list):
        return "wrapper-events"
    if "task_id" in d and "outcomes" in d:
        return "dataset-v1"
    if "event_id" in d or "market_implied_p_yes" in d:
        return "pastcast-v1"
    return "unknown"


# -- Core normalizer ------------------------------------------------------


def normalize_one(raw: Dict[str, Any]) -> ForecastTask:
    """Normalize a single raw event dict. Raises SchemaError on missing
    required fields."""
    if not isinstance(raw, dict):
        raise SchemaError(f"event must be a dict, got {type(raw).__name__}", raw=raw)

    task_id = _get_str(raw, "task_id") or _get_str(raw, "id") or _get_str(raw, "event_id") or _get_str(raw, "market_id")
    if task_id is None:
        raise SchemaError("missing task_id / id / event_id / market_id", raw=raw)

    title = (
        _get_str(raw, "title")
        or _get_str(raw, "question")
        or _get_str(raw, "name")
    )
    if title is None:
        raise SchemaError(f"missing title/question/name on task {task_id}", raw=raw)

    outcomes = _get_outcomes(raw)
    if outcomes is None or len(outcomes) < 2:
        # Pastcast/event-only payloads sometimes ship binary tasks
        # implicitly. Allow ["Yes", "No"] as a fallback.
        if raw.get("market_implied_p_yes") is not None:
            outcomes = ["Yes", "No"]
        else:
            raise SchemaError(
                f"missing or invalid outcomes on task {task_id}: {raw.get('outcomes')!r}",
                raw=raw,
            )

    is_binary = len(outcomes) == 2
    yes_label = infer_yes_label(outcomes) if is_binary else None
    if is_binary and yes_label is None:
        # Caller may want to fall back to outcomes[0] but we mark it
        # in metadata so the trace can flag the ambiguity.
        yes_label = outcomes[0]
        meta_extra = {"yes_label_inference": "ambiguous_fallback_outcomes_0"}
    else:
        meta_extra = {}

    resolved = _get_resolved(raw, outcomes)
    metadata = _get_metadata(raw)
    metadata.update(meta_extra)

    close_time = metadata.get("close_time")
    if isinstance(close_time, str) and close_time.strip():
        from datetime import datetime
        candidate = close_time.replace("Z", "+00:00")
        try:
            datetime.fromisoformat(candidate)
        except ValueError:
            raise SchemaError(
                f"invalid close_time timestamp on task {task_id}: {close_time!r}",
                raw=raw,
            )

    return ForecastTask(
        task_id=task_id,
        title=title,
        outcomes=tuple(outcomes),
        yes_label=yes_label,
        is_binary=is_binary,
        context=_get_str(raw, "context"),
        source=_get_str(raw, "source"),
        metadata=metadata,
        resolved=resolved,
        raw_event_hash=compute_raw_hash(raw),
        schema_version_seen=detect_schema_version(raw),
    )


def normalize_iter(raws: Iterable[Dict[str, Any]]) -> Iterable[ForecastTask]:
    """Yield normalized tasks; collect-or-raise is the caller's choice."""
    for r in raws:
        yield normalize_one(r)


def unwrap_payload(payload: Any) -> List[Dict[str, Any]]:
    """Pull a list of raw event dicts out of any reasonable payload shape:

    - bare list of events
    - {"tasks": [...]}
    - {"events": [...]}
    - {"data": [...]}
    - single event dict
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("tasks", "events", "data"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return inner
        # Single-event dict
        if "task_id" in payload or "id" in payload or "event_id" in payload:
            return [payload]
    raise SchemaError(f"could not unwrap events payload of type {type(payload).__name__}")
