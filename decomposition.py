"""Decomposition forecaster.

The model returns strict JSON with a base rate, a raw probability of YES
before market consideration, source quality, and a `should_shrink` hint.
The code, not the model, decides the final number:

    raw -> clamp -> optional shrink (per should_shrink) -> blend with market -> edge gate

On parse failure we return a fallback shrunk toward the market price.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional

from calibrator import shrink


log = logging.getLogger("prophet-hacks.decomposition")


# Canonical v2 decomposition prompt. Single self-contained user prompt;
# the LLM client passes an empty system prompt alongside this. JSON braces
# in the template are doubled so str.format leaves them as single braces.
DECOMPOSITION_PROMPT_TEMPLATE = """\
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
  "base_rate_rationale": string,
  "evidence_for_yes": [string],
  "evidence_for_no": [string],
  "stale_or_weak_evidence": [string],
  "key_uncertainties": [string],
  "time_to_resolution_risk": "low|medium|high",
  "source_quality": 0.0-1.0,
  "raw_p_yes_before_market": 0.01-0.99,
  "should_shrink": true|false,
  "shrink_reason": string,
  "final_rationale": string
}}
"""


# Minimal jsonschema-style structure for offline validation.
DECOMPOSITION_SCHEMA: dict = {
    "type": "object",
    "required": [
        "base_rate",
        "raw_p_yes_before_market",
        "source_quality",
        "should_shrink",
        "time_to_resolution_risk",
    ],
    "properties": {
        "base_rate": {"type": "number"},
        "base_rate_rationale": {"type": "string"},
        "evidence_for_yes": {"type": "array", "items": {"type": "string"}},
        "evidence_for_no": {"type": "array", "items": {"type": "string"}},
        "stale_or_weak_evidence": {"type": "array", "items": {"type": "string"}},
        "key_uncertainties": {"type": "array", "items": {"type": "string"}},
        "time_to_resolution_risk": {"type": "string"},
        "source_quality": {"type": "number"},
        "raw_p_yes_before_market": {"type": "number"},
        "should_shrink": {"type": "boolean"},
        "shrink_reason": {"type": "string"},
        "final_rationale": {"type": "string"},
    },
}


_REQUIRED_FIELDS = (
    "base_rate",
    "raw_p_yes_before_market",
    "source_quality",
    "should_shrink",
    "time_to_resolution_risk",
)


def _market_mid(market: Any) -> float:
    quote = getattr(market, "quote", None)
    if quote is None:
        return 0.5
    bid = float(getattr(quote, "best_bid", 0) or 0)
    ask = float(getattr(quote, "best_ask", 0) or 0)
    return (bid + ask) / 2.0


def build_user_prompt(market: Any, evidence_block: dict, market_meta: dict) -> str:
    """Render the canonical decomposition prompt with this market's values."""
    question = getattr(market, "question", "") or ""
    criteria = (
        getattr(market, "resolution_criteria", None)
        or getattr(market, "resolution_description", None)
        or ""
    )

    quote = getattr(market, "quote", None)
    if quote is not None:
        bid = float(getattr(quote, "best_bid", 0) or 0)
        ask = float(getattr(quote, "best_ask", 0) or 0)
    else:
        bid = 0.0
        ask = 0.0
    mid = (bid + ask) / 2.0

    resolution_time = getattr(market, "resolution_time", None)
    resolution_time_str = (
        resolution_time.isoformat() if resolution_time is not None else "unknown"
    )

    summary = (evidence_block or {}).get("summary", "") or "none"

    return DECOMPOSITION_PROMPT_TEMPLATE.format(
        question=question,
        resolution_criteria=criteria,
        yes_bid=f"{bid:.3f}",
        yes_ask=f"{ask:.3f}",
        market_mid=f"{mid:.3f}",
        resolution_time=resolution_time_str,
        evidence_block=summary,
    )


def _extract_json(text: str) -> Optional[dict]:
    """Three-level JSON extraction, layered from Gemini's parser.

    1. Direct json.loads on the trimmed string.
    2. Narrow regex anchored on "p_yes" to skip preamble/postscript noise.
    3. Greedy {...} block. Tolerant of trailing prose after the JSON.
    """
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # Level 2: anchor on a key from the canonical v2 schema to skip preamble.
    narrow = re.search(
        r'\{[^{}]*"raw_p_yes_before_market"\s*:\s*[\d.]+[^{}]*\}', text,
    )
    if narrow:
        try:
            return json.loads(narrow.group(0))
        except json.JSONDecodeError:
            pass
    # Level 3: greedy first-{ to last-} block for models that wrap JSON in prose.
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _validate(parsed: Any) -> bool:
    if not isinstance(parsed, dict):
        return False
    for key in _REQUIRED_FIELDS:
        if key not in parsed:
            return False
    try:
        raw_p = float(parsed["raw_p_yes_before_market"])
        base = float(parsed["base_rate"])
        sq = float(parsed["source_quality"])
    except (TypeError, ValueError):
        return False
    if not (0.0 < raw_p < 1.0):
        return False
    if not (0.0 <= base <= 1.0):
        return False
    if not (0.0 <= sq <= 1.0):
        return False
    if not isinstance(parsed["should_shrink"], bool):
        return False
    if parsed["time_to_resolution_risk"] not in {"low", "medium", "high"}:
        return False
    return True


def _fallback(market: Any, reason: str) -> dict:
    """Shrunk-toward-market fallback when the model output cannot be parsed."""
    market_p = _market_mid(market) or 0.5
    p = shrink(market_p, tau=0.5, anchor=market_p)
    return {
        "base_rate": market_p,
        "base_rate_rationale": "fallback to market midpoint",
        "evidence_for_yes": [],
        "evidence_for_no": [],
        "stale_or_weak_evidence": [],
        "key_uncertainties": ["decomposition parse failure"],
        "time_to_resolution_risk": "medium",
        "source_quality": 0.0,
        "raw_p_yes_before_market": p,
        "should_shrink": True,
        "shrink_reason": "parse_failure",
        "final_rationale": f"decomposition fallback: {reason}",
        "parse_failed": True,
    }


def decompose(
    market: Any,
    evidence_block: dict,
    market_meta: dict,
    llm_client: Callable[[str, str], str],
) -> dict:
    """Call the LLM, parse strict JSON, fall back to a shrunk forecast on error.

    `llm_client` is a callable that takes (system_prompt, user_prompt) and
    returns the raw model text. The canonical prompt is self-contained, so
    the system prompt is empty.
    """
    user_prompt = build_user_prompt(market, evidence_block, market_meta)
    try:
        raw = llm_client("", user_prompt)
    except Exception as e:  # noqa: BLE001
        log.warning("decomposition LLM call failed: %s", e)
        return _fallback(market, f"llm call failed: {e}")

    parsed = _extract_json(raw or "")
    if not _validate(parsed):
        return _fallback(market, "invalid or missing JSON")

    p = float(parsed["raw_p_yes_before_market"])
    if p < 0.01:
        p = 0.01
    elif p > 0.99:
        p = 0.99
    parsed["raw_p_yes_before_market"] = p
    parsed["parse_failed"] = False
    return parsed


__all__ = [
    "DECOMPOSITION_PROMPT_TEMPLATE",
    "DECOMPOSITION_SCHEMA",
    "build_user_prompt",
    "decompose",
]
