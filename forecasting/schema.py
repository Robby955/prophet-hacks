"""Canonical internal schema for forecasting tasks.

Everything our forecasting / calibration / risk-gate code touches goes
through `ForecastTask`. Raw inputs (events.json, tasks.jsonl, server
JSON payloads, third-party fixtures) are normalized to this shape at
the edge via `forecasting.normalize`. The forecaster never sees raw
event dicts.

Invariants enforced here (validation, not assumption):
- task_id, title, outcomes are required and well-typed.
- outcomes is a list of strings, deduplicated, with length >= 2.
- resolved_outcome.value (when present) is a list of strings that is a
  subset of outcomes.
- For binary tasks we track which outcome is the "YES" label so the
  forecaster's `p_yes` semantics stay unambiguous regardless of
  whether outcomes are ["Yes", "No"] or ["Above", "Below"].

Reference shape (one tasks.jsonl row from ai-prophet-datasets):

    {
      "task_id": "KXBTC-25MAR21-B90000",
      "title": "Will BTC exceed $90,000 by March 21?",
      "outcomes": ["Yes", "No"],
      "source": "kalshi",
      "context": "...",
      "metadata": {"market_ticker": "...", "close_time": "...", "category": "..."},
      "resolved_outcome": {
        "value": ["Yes"],
        "resolved_at": "2026-03-21T00:07:09Z",
        "source": "KXBTC-25MAR21-B90000"
      }
    }
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# -- Errors ---------------------------------------------------------------


class SchemaError(ValueError):
    """Raised when a raw event cannot be normalized into ForecastTask."""

    def __init__(self, message: str, raw: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.raw = raw


class PredictionOutputError(ValueError):
    """Raised when a predictor returns an invalid p_yes / rationale shape."""


# -- ForecastTask ---------------------------------------------------------


@dataclass(frozen=True)
class ResolvedOutcome:
    value: Tuple[str, ...]
    resolved_at: Optional[str] = None
    source: Optional[str] = None


@dataclass(frozen=True)
class ForecastTask:
    task_id: str
    title: str
    outcomes: Tuple[str, ...]
    yes_label: Optional[str]  # the outcome string that maps to p_yes; None for multi-outcome
    is_binary: bool
    context: Optional[str] = None
    source: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    resolved: Optional[ResolvedOutcome] = None
    raw_event_hash: str = ""
    schema_version_seen: Optional[str] = None

    @property
    def category(self) -> str:
        meta_cat = (self.metadata or {}).get("category")
        if isinstance(meta_cat, str) and meta_cat:
            return meta_cat
        return "other"

    @property
    def close_time(self) -> Optional[str]:
        meta = self.metadata or {}
        for k in ("close_time", "resolution_time", "deadline"):
            v = meta.get(k)
            if isinstance(v, str) and v:
                return v
        return None

    @property
    def is_resolved(self) -> bool:
        return self.resolved is not None and bool(self.resolved.value)

    def binary_outcome_int(self) -> Optional[int]:
        """Return 1 if YES resolved, 0 if NO resolved, None if unresolved
        or non-binary."""
        if not self.is_binary or not self.is_resolved or self.yes_label is None:
            return None
        value = self.resolved.value if self.resolved else ()
        if not value:
            return None
        return 1 if value[0] == self.yes_label else 0


# -- Helpers --------------------------------------------------------------


YES_TOKENS = frozenset(
    {"yes", "true", "above", "over", "more", "happens", "win", "hit"}
)
NO_TOKENS = frozenset(
    {"no", "false", "below", "under", "less", "doesn't", "lose", "miss"}
)


def infer_yes_label(outcomes: List[str]) -> Optional[str]:
    """Best-effort: which outcome is the YES side?

    Returns None when the task is multi-outcome (>2) or when neither side
    obviously matches a YES token. Caller can fall back to outcomes[0]
    if it must, but should log that ambiguity to the trace.
    """
    if len(outcomes) != 2:
        return None
    lower = [o.strip().lower() for o in outcomes]
    for i, tok in enumerate(lower):
        if tok in YES_TOKENS:
            return outcomes[i]
    for i, tok in enumerate(lower):
        if tok in NO_TOKENS:
            other = 0 if i == 1 else 1
            return outcomes[other]
    # Heuristic: prefixed comparators
    for i, tok in enumerate(lower):
        if tok.startswith(("above", "over", "more than", ">=", ">", "greater")):
            return outcomes[i]
    return None


def compute_raw_hash(raw: Dict[str, Any]) -> str:
    """Stable SHA-256 over the canonical-JSON of the raw event.

    Used to fingerprint every prediction's input for reproducibility.
    Sorted keys + compact separators -> identical input always hashes
    to the same string.
    """
    blob = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# -- Prediction output schema --------------------------------------------


@dataclass(frozen=True)
class PredictionOutput:
    """What the agent must return per task. The Prophet Arena CLI sends
    each event to a local module or HTTP endpoint and expects this shape
    in return:

        {"p_yes": 0.72, "rationale": "..."}

    For multi-outcome tasks, `p_yes` semantics are ambiguous; the
    Prophet Arena scoring still uses p_yes for the YES outcome in
    a binary mapping. We extend with `p_distribution` (optional) for
    multi-outcome cases so future organizer schemas don't break us.
    """

    p_yes: float
    rationale: str = ""
    p_distribution: Optional[Dict[str, float]] = None

    def to_arena_json(self) -> Dict[str, Any]:
        """Serialize to the on-the-wire shape Prophet Arena scoring expects."""
        return {"p_yes": float(self.p_yes), "rationale": self.rationale}


def validate_prediction_output(d: Dict[str, Any]) -> PredictionOutput:
    """Strict parser: raises PredictionOutputError on any issue."""
    if not isinstance(d, dict):
        raise PredictionOutputError(f"expected dict, got {type(d).__name__}")
    if "p_yes" not in d:
        raise PredictionOutputError("missing 'p_yes'")
    p_yes = d["p_yes"]
    try:
        p = float(p_yes)
    except (TypeError, ValueError):
        raise PredictionOutputError(f"p_yes is not numeric: {p_yes!r}")
    if not (0.01 <= p <= 0.99):
        raise PredictionOutputError(
            f"p_yes={p} outside [0.01, 0.99] (Prophet Arena scoring constraint)"
        )
    rationale = d.get("rationale", "")
    if rationale is None:
        rationale = ""
    if not isinstance(rationale, str):
        raise PredictionOutputError(f"rationale must be string or null, got {type(rationale).__name__}")
    p_dist = d.get("p_distribution")
    if p_dist is not None:
        if not isinstance(p_dist, dict):
            raise PredictionOutputError("p_distribution must be a dict of {outcome: probability}")
        for k, v in p_dist.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise PredictionOutputError(f"p_distribution[{k!r}] not numeric")
            if not (0.0 <= fv <= 1.0):
                raise PredictionOutputError(f"p_distribution[{k!r}]={fv} outside [0, 1]")
    return PredictionOutput(p_yes=p, rationale=rationale, p_distribution=p_dist)
