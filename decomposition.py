"""Decomposition forecaster.

The model is asked to break the market into sub-events with probabilities,
their dependency structure, and the marginal probability of YES. The
output is strict JSON. The final probability that goes into the ensemble
is read from the JSON, NOT computed by the model on a separate turn -
the code, not the model, owns the final number.

On parse failure we shrink toward the market price rather than crash.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional

from calibrator import shrink


log = logging.getLogger("prophet-hacks.decomposition")


# Strict JSON schema in plain English so the model can comply without a
# tool-call surface. The pipeline parses the output with json.loads.
DECOMPOSITION_SYSTEM_PROMPT = """\
You are a calibrated probabilistic forecaster for prediction markets.

Decompose the market into the smallest set of sub-events whose joint
truth determines YES. For each sub-event give:
  - id            (short slug, ascii)
  - statement     (one sentence)
  - p             (marginal probability the sub-event resolves true, in (0, 1))
  - depends_on    (list of sub-event ids this sub-event is conditional on; can be empty)
  - rationale     (one short sentence)

Then state the combination rule that maps the sub-events to YES:
  - rule          (one of: "all_of", "any_of", "chain", "custom")
  - rule_detail   (one sentence describing how the sub-events combine; required if rule == "custom")

Finally state the marginal probability of YES (p_yes), and a 1-3 sentence
overall rationale.

Calibration scale:
  0.50 = no view (default when uncertain)
  0.60 = slight lean, weak evidence
  0.70 = real view, concrete reasoning, multiple signals
  0.80 = strong evidence, clear mechanism
  0.90 = near-certain, authoritative source

Never output 0.99 or 0.01 unless the event is mechanically determined.
Prefer 0.50 over fake precision.

Output ONLY valid JSON. The schema is:

{
  "sub_events": [
    {
      "id": "string",
      "statement": "string",
      "p": 0.0,
      "depends_on": ["string", "..."],
      "rationale": "string"
    }
  ],
  "combination": {
    "rule": "all_of | any_of | chain | custom",
    "rule_detail": "string"
  },
  "p_yes": 0.0,
  "rationale": "string"
}
"""


# Optional minimal jsonschema-style structure for offline validation.
DECOMPOSITION_SCHEMA: dict = {
    "type": "object",
    "required": ["sub_events", "combination", "p_yes", "rationale"],
    "properties": {
        "sub_events": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "statement", "p", "depends_on", "rationale"],
            },
        },
        "combination": {
            "type": "object",
            "required": ["rule", "rule_detail"],
        },
        "p_yes": {"type": "number"},
        "rationale": {"type": "string"},
    },
}


def build_user_prompt(market: Any, evidence_block: dict, market_meta: dict) -> str:
    """Construct the per-market user prompt. Includes evidence summary if any."""
    parts: list[str] = []
    question = getattr(market, "question", "") or ""
    parts.append(f"MARKET QUESTION: {question}")

    description = getattr(market, "description", "") or ""
    if description:
        parts.append(f"\nDESCRIPTION: {description[:800]}")

    criteria = (
        getattr(market, "resolution_criteria", None)
        or getattr(market, "resolution_description", None)
        or ""
    )
    if criteria:
        parts.append(f"\nRESOLUTION CRITERIA: {criteria[:600]}")

    parts.append(f"\nDOMAIN: {market_meta.get('domain', 'other')}")
    parts.append(f"HORIZON HOURS: {market_meta.get('horizon_hours', 0):.1f}")
    parts.append(f"RESOLUTION TYPE: {market_meta.get('resolution_type', 'binary')}")

    quote = getattr(market, "quote", None)
    if quote is not None:
        bid = float(getattr(quote, "best_bid", 0) or 0)
        ask = float(getattr(quote, "best_ask", 0) or 0)
        mid = (bid + ask) / 2.0
        parts.append(
            f"\nMARKET QUOTE: bid={bid:.3f} ask={ask:.3f} mid={mid:.3f}"
        )

    summary = (evidence_block or {}).get("summary", "")
    if summary:
        parts.append(f"\nEVIDENCE SUMMARY:\n{summary}")

    parts.append(
        "\nReturn ONLY the JSON object that matches the schema in the system prompt."
    )
    return "\n".join(parts)


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
    # Level 2: p_yes-anchored block (handles preamble/postscript).
    narrow = re.search(r'\{[^{}]*"p_yes"\s*:\s*[\d.]+[^{}]*\}', text)
    if narrow:
        try:
            return json.loads(narrow.group(0))
        except json.JSONDecodeError:
            pass
    # Level 3: greedy first { to last }.
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
    if "p_yes" not in parsed:
        return False
    try:
        p = float(parsed["p_yes"])
    except (TypeError, ValueError):
        return False
    if not (0.0 < p < 1.0):
        return False
    sub = parsed.get("sub_events")
    combo = parsed.get("combination")
    if not isinstance(sub, list):
        return False
    if not isinstance(combo, dict):
        return False
    return True


def _fallback(market: Any, reason: str) -> dict:
    """Shrunk-toward-market fallback when the model output cannot be parsed."""
    quote = getattr(market, "quote", None)
    if quote is not None:
        bid = float(getattr(quote, "best_bid", 0) or 0)
        ask = float(getattr(quote, "best_ask", 0) or 0)
        market_p = (bid + ask) / 2.0
    else:
        market_p = 0.5
    p = shrink(market_p, tau=0.5, anchor=market_p)
    return {
        "sub_events": [],
        "combination": {"rule": "custom", "rule_detail": "fallback"},
        "p_yes": p,
        "rationale": f"decomposition fallback: {reason}",
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
    returns the raw model text. Keeping it injected makes the module
    trivially testable: tests pass a lambda that returns canned JSON.
    """
    user_prompt = build_user_prompt(market, evidence_block, market_meta)
    try:
        raw = llm_client(DECOMPOSITION_SYSTEM_PROMPT, user_prompt)
    except Exception as e:  # noqa: BLE001
        log.warning("decomposition LLM call failed: %s", e)
        return _fallback(market, f"llm call failed: {e}")

    parsed = _extract_json(raw or "")
    if not _validate(parsed):
        return _fallback(market, "invalid or missing JSON")

    # Clamp p_yes into the open interval for downstream calibration.
    p = float(parsed["p_yes"])
    if p <= 0.0:
        p = 0.01
    elif p >= 1.0:
        p = 0.99
    parsed["p_yes"] = p
    parsed["parse_failed"] = False
    return parsed


__all__ = [
    "DECOMPOSITION_SYSTEM_PROMPT",
    "DECOMPOSITION_SCHEMA",
    "build_user_prompt",
    "decompose",
]
