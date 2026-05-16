"""Source credibility scoring for retrieved evidence.

Translates the credibility hierarchy from the Prophet Hacks strategy
memo (official > primary > news > analysis > blog > social) into a
single source_quality scalar in [0, 1] that feeds the calibrator.

Staleness penalty: an old "official" source can be worse than a fresh
"primary" one. We multiply each source's base credibility by a
staleness multiplier and ensemble across all retrieved sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional


_TYPE_CREDIBILITY = {
    "official": 1.0,
    "primary": 0.85,
    "news": 0.60,
    "analysis": 0.50,
    "blog": 0.30,
    "social": 0.20,
    "market": 0.55,
    "unknown": 0.40,
}


@dataclass(frozen=True)
class Source:
    url: str
    source_type: str  # one of the keys in _TYPE_CREDIBILITY
    published_at: Optional[datetime] = None
    retrieved_at: Optional[datetime] = None
    supports_yes: bool = False
    supports_no: bool = False
    title: str = ""
    summary: str = ""


def base_credibility(source_type: str) -> float:
    """Map source_type to baseline credibility. Unknown types fall back."""
    return _TYPE_CREDIBILITY.get(source_type.lower(), _TYPE_CREDIBILITY["unknown"])


def staleness_multiplier(
    published_at: Optional[datetime],
    forecast_time: Optional[datetime],
    horizon_to_resolution: Optional[float] = None,
) -> float:
    """Penalize sources by how stale they are relative to the forecast time.

    If horizon_to_resolution (hours) is provided, staleness is measured
    relative to that horizon — a source 1 day old matters less when the
    market resolves in a year than when it resolves tomorrow.

    Returns a multiplier in [0.3, 1.0]; we never zero out a source entirely
    because even stale official sources carry some base-rate information.
    """
    if published_at is None or forecast_time is None:
        return 0.8
    age_seconds = (forecast_time - published_at).total_seconds()
    if age_seconds <= 0:
        return 1.0
    age_hours = age_seconds / 3600.0
    if horizon_to_resolution is None or horizon_to_resolution <= 0:
        # Default: half-life of ~14 days for unknown-horizon markets.
        half_life_hours = 14 * 24
    else:
        # Source is stale when its age approaches the time-to-resolution.
        half_life_hours = max(6.0, horizon_to_resolution * 0.5)
    decay = 0.5 ** (age_hours / half_life_hours)
    return max(0.3, decay)


def score_source(
    source: Source,
    forecast_time: Optional[datetime] = None,
    horizon_to_resolution: Optional[float] = None,
) -> float:
    """Combined credibility for a single source."""
    base = base_credibility(source.source_type)
    stale = staleness_multiplier(
        source.published_at, forecast_time, horizon_to_resolution
    )
    return max(0.0, min(1.0, base * stale))


def ensemble_source_quality(
    sources: Iterable[Source],
    forecast_time: Optional[datetime] = None,
    horizon_to_resolution: Optional[float] = None,
) -> float:
    """Aggregate across all retrieved sources into a single quality scalar.

    Strategy: weighted mean where each source's weight is its own
    credibility. This rewards having even one excellent source while not
    being dragged down too hard by a pile of noisy ones.

    Returns 0.0 if no sources are provided.
    """
    scores = [
        score_source(s, forecast_time, horizon_to_resolution) for s in sources
    ]
    if not scores:
        return 0.0
    total_weight = sum(scores)
    if total_weight <= 0:
        return 0.0
    weighted = sum(s * s for s in scores)
    return max(0.0, min(1.0, weighted / total_weight))


def source_disagreement(sources: Iterable[Source]) -> float:
    """Fraction of credibility-weighted sources that point opposite the
    plurality direction. Useful for the "sources conflict" escalation
    condition in the v2.1 strategy.

    Returns 0.0 when all sources point the same way (or have no direction),
    1.0 when YES-supporting and NO-supporting weights are equal.
    """
    sources = list(sources)
    if not sources:
        return 0.0
    yes_w = sum(score_source(s) for s in sources if s.supports_yes)
    no_w = sum(score_source(s) for s in sources if s.supports_no)
    total = yes_w + no_w
    if total <= 0:
        return 0.0
    minor = min(yes_w, no_w)
    return (2.0 * minor) / total
