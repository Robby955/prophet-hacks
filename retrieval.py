"""Retrieval gate + evidence assembly.

The gate skips retrieval when the question is trivially decidable from the
market metadata alone (pure-numeric thresholds with the metric already on
the wire, or markets that have effectively resolved). When retrieval runs
it produces a structured evidence block; on Saturday we stub the block
using market description + resolution criteria so the rest of the pipeline
is exercised end-to-end without a live web call.

Credibility hierarchy used to score sources (high -> low):
    official  - the body that decides the outcome (league, agency, central bank)
    primary   - the entity in question (company filing, candidate statement)
    news      - major outlets reporting on the event
    analysis  - opinion or commentary
    social    - posts, threads, unverified rumor

Production retrieval is intentionally narrow: 1-3 sources per market,
fetched against a short allowlist, never a broad crawl.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


SOURCE_TYPES: tuple[str, ...] = ("official", "primary", "news", "analysis", "social")

# Source-type -> baseline credibility weight in [0, 1].
_CREDIBILITY_BY_TYPE: dict[str, float] = {
    "official": 1.00,
    "primary": 0.85,
    "news": 0.70,
    "analysis": 0.45,
    "social": 0.20,
}


@dataclass
class EvidenceItem:
    url: str
    source_type: str
    published_at: Optional[str]
    retrieved_at: str
    supports_yes: bool
    supports_no: bool
    credibility: float
    staleness: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvidenceBlock:
    items: list[EvidenceItem] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "items": [it.to_dict() for it in self.items],
            "summary": self.summary,
            "quality": self.quality(),
        }

    def quality(self) -> float:
        """Aggregate evidence quality in [0, 1].

        Quality is the credibility-weighted, staleness-discounted average
        of contributing sources, capped at the count-weighted ceiling.
        """
        if not self.items:
            return 0.0
        weighted = 0.0
        total = 0.0
        for it in self.items:
            w = max(0.0, it.credibility) * max(0.0, 1.0 - it.staleness)
            weighted += w
            total += 1.0
        # 3 well-credentialed, fresh sources -> ~1.0
        return min(1.0, weighted / 3.0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_resolved(market: Any) -> bool:
    res = getattr(market, "resolution_time", None)
    if isinstance(res, datetime):
        dt = res if res.tzinfo else res.replace(tzinfo=timezone.utc)
        return dt <= datetime.now(timezone.utc)
    return False


def _is_pure_numeric_trivial(market: Any, market_meta: dict) -> bool:
    """The metric is on the wire already, no outside info would add value.

    Right now this is a conservative stub: trigger only when the market is
    flagged as 'price' or 'numeric_metric' with a resolution_type of
    'threshold' or 'range'. Production can widen the predicate.
    """
    topic = (getattr(market, "topic", "") or "").lower()
    if "price" in topic and market_meta.get("resolution_type") in {"threshold", "range"}:
        return True
    return False


def should_retrieve(market: Any, market_meta: dict) -> bool:
    """Gate. Return False when retrieval would not improve the forecast."""
    if _is_resolved(market):
        return False
    if _is_pure_numeric_trivial(market, market_meta):
        return False
    return True


def _stub_evidence(market: Any) -> EvidenceBlock:
    """Saturday stub.

    Builds a single 'primary' evidence item from market.description and
    resolution criteria. Shape matches a production retrieval result so
    downstream code does not need to special-case Saturday.
    """
    desc = getattr(market, "description", "") or ""
    criteria = (
        getattr(market, "resolution_criteria", None)
        or getattr(market, "resolution_description", None)
        or ""
    )
    summary_parts = []
    if desc:
        summary_parts.append(desc[:600])
    if criteria:
        summary_parts.append(f"Resolution criteria: {criteria[:400]}")
    summary = "\n".join(summary_parts)

    items: list[EvidenceItem] = []
    if summary:
        items.append(
            EvidenceItem(
                url=f"market://{getattr(market, 'market_id', 'unknown')}",
                source_type="primary",
                published_at=None,
                retrieved_at=_now_iso(),
                supports_yes=False,
                supports_no=False,
                credibility=_CREDIBILITY_BY_TYPE["primary"],
                staleness=0.0,
            )
        )
    return EvidenceBlock(items=items, summary=summary)


def gather_evidence(market: Any, market_meta: dict) -> dict:
    """Run the gate, then either return an empty block or assemble evidence.

    Returns a dict with `block` (EvidenceBlock as plain dict) and
    `quality` (float) so the rest of the pipeline can stay structural.
    """
    if not should_retrieve(market, market_meta):
        return {
            "retrieved": False,
            "block": EvidenceBlock().to_dict(),
            "quality": 0.0,
        }
    block = _stub_evidence(market)
    return {
        "retrieved": True,
        "block": block.to_dict(),
        "quality": block.quality(),
    }


__all__ = [
    "SOURCE_TYPES",
    "EvidenceItem",
    "EvidenceBlock",
    "should_retrieve",
    "gather_evidence",
]
