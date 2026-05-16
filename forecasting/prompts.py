"""Canonical forecast prompts.

The model REASONS. The code CALIBRATES. The model NEVER decides
trade size. This file owns the prompt strings + the strict-JSON
schema the parser expects.

Locked by the v6 playbook Section 19. Any change to the schema
fields here also requires updating `forecasting.decomposition`'s
parser + the `tests/test_pipeline_integration.py` canned JSON.
"""

from __future__ import annotations

from typing import Sequence


CANONICAL_FORECAST_PROMPT_TEMPLATE = """\
You are forecasting a binary event.
Question: {question}
Resolution criteria: {resolution_criteria}
Market information:
  YES bid: {yes_bid}
  YES ask: {yes_ask}
  Market midpoint: {market_mid}
  Resolution time: {resolution_time}
Retrieved evidence: {evidence_block}

Return strict JSON:
{{
  "base_rate": number,
  "base_rate_note": "short",
  "evidence_for_yes": ["..."],
  "evidence_for_no": ["..."],
  "weak_or_stale_evidence": ["..."],
  "key_uncertainties": ["..."],
  "source_quality": 0.0,
  "raw_p_yes_before_market_blend": 0.0,
  "confidence": "low|medium|high",
  "reasoning_to_probability": "why this number follows",
  "trade_caution": "short"
}}
"""


CANONICAL_BIDIRECTIONAL_NO_TEMPLATE = """\
You are now forecasting the NO side of the same binary event you just
saw. Use the same evidence. Return strict JSON:
{{
  "raw_p_no": 0.0,
  "reasoning_to_probability_no": "why this number follows"
}}
"""


REQUIRED_FIELDS = (
    "base_rate",
    "raw_p_yes_before_market_blend",
    "source_quality",
    "confidence",
)

OPTIONAL_LIST_FIELDS = (
    "evidence_for_yes",
    "evidence_for_no",
    "weak_or_stale_evidence",
    "key_uncertainties",
)

OPTIONAL_STRING_FIELDS = (
    "base_rate_note",
    "reasoning_to_probability",
    "trade_caution",
)


def format_evidence_block(sources: Sequence[dict]) -> str:
    """Render a list of source dicts into a deterministic, compact block
    for the prompt. Each source becomes one bullet with its source_type
    + retrieved_at + summary.

    Keeps under ~3K tokens by truncating summaries to 200 chars each
    and capping at 6 sources.
    """
    if not sources:
        return "(no retrieved evidence)"
    lines = []
    for src in sources[:6]:
        st = src.get("source_type", "unknown")
        ret = src.get("retrieved_at", "?")
        summary = (src.get("summary") or "")[:200]
        url = src.get("url", "")
        lines.append(f"- [{st} @ {ret}] {summary} ({url})")
    return "\n".join(lines)


def build_forecast_prompt(
    *,
    question: str,
    resolution_criteria: str,
    yes_bid: float,
    yes_ask: float,
    market_mid: float,
    resolution_time: str,
    sources: Sequence[dict],
) -> str:
    return CANONICAL_FORECAST_PROMPT_TEMPLATE.format(
        question=question,
        resolution_criteria=resolution_criteria,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        market_mid=market_mid,
        resolution_time=resolution_time,
        evidence_block=format_evidence_block(sources),
    )
