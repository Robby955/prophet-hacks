"""Prophet Arena forecasting-track agent.

The forecasting track scores probability predictions against actual outcomes
using Brier score. Each event has a `market_ticker` and a list of `outcomes`
of variable length (2..30 in the sample-resolved dataset). The implicit
binary question per market is: **"Is outcomes[0] the resolved winner?"**
The single `p_yes` per market is the probability that outcomes[0] resolves.

CLI integration:
    # Local predict (function call, no HTTP):
    prophet forecast predict --events events.json \
        --local forecast_track:predict_single_llm -o predictions.json

    # Evaluate against resolved dataset:
    prophet forecast evaluate --submission predictions.json --actuals actuals.json

Variants exposed (each is a `predict(event: dict) -> dict`):
- predict_uniform_prior: deterministic 1/len(outcomes), clamped. No LLM cost.
- predict_single_llm:    one Anthropic Sonnet 4.6 call per event.

Both return ``{"p_yes": float ∈ [0.01, 0.99], "rationale": str}``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv


load_dotenv()

log = logging.getLogger("prophet-hacks.forecast_track")

P_YES_MIN: float = 0.01
P_YES_MAX: float = 0.99

_FORECAST_MODEL: str = os.environ.get(
    "PROPHET_FORECAST_TRACK_MODEL", "claude-sonnet-4-6",
)


def _clamp(p: float) -> float:
    if p < P_YES_MIN:
        return P_YES_MIN
    if p > P_YES_MAX:
        return P_YES_MAX
    return p


def _yes_outcome(event: dict) -> str | None:
    """The first listed outcome is the implicit YES condition.

    For 2-outcome markets ("Who won X vs Y?") outcomes[0] is one candidate.
    For multi-outcome markets ("Who's Netflix's next roast subject?")
    outcomes[0] is the listed favorite. Either way, the binary p_yes is
    `P(resolved_outcome == outcomes[0])`.
    """
    outs = event.get("outcomes") or []
    return outs[0] if outs else None


# ---------------------------------------------------------------------------
# Variant: uniform-prior baseline (no LLM)
# ---------------------------------------------------------------------------

def predict_uniform_prior(event: dict) -> dict:
    """Deterministic baseline: 1/len(outcomes), clamped to [0.01, 0.99].

    Cheap control; should beat random (0.5) on multi-outcome events because
    it correctly assigns low p_yes when there are many alternatives.
    """
    outs = event.get("outcomes") or []
    n = len(outs)
    p = 0.5 if n <= 0 else _clamp(1.0 / n)
    return {
        "p_yes": p,
        "rationale": (
            f"uniform prior over {n} outcomes; p = 1/{n} = {p:.3f}"
            if n > 0
            else "no outcomes listed; defaulting to 0.5"
        ),
    }


# ---------------------------------------------------------------------------
# Variant: single Sonnet 4.6 call
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a calibrated probabilistic forecaster for prediction markets.

Your task: estimate the probability that the FIRST listed outcome is the
resolved winner of the event. This is the implicit binary YES condition.

Rules:
- Output ONLY valid JSON: {"p_yes": <float>, "rationale": "<string>"}
- p_yes must be between 0.01 and 0.99.
- Use a calibration scale:
  0.50 = no view; default for genuine uncertainty.
  0.60 = slight lean; weak base rate or partial evidence.
  0.70 = real view; concrete reasoning, multiple consistent signals.
  0.80 = strong view; hard evidence, clear mechanism.
  0.90 = near-certain; mechanically determined or authoritative source.
- Never emit 0.01 or 0.99 unless mechanically determined.
- For N-outcome markets, the uninformed prior on outcomes[0] is 1/N. Move
  off the prior only when you have a concrete reason.
- The rationale is 1-3 sentences explaining your reasoning.
"""


def _build_user_prompt(event: dict) -> str:
    yes_outcome = _yes_outcome(event)
    outs = event.get("outcomes") or []
    n = len(outs)
    parts = [f"EVENT: {event.get('title', '?')}"]
    if event.get("subtitle"):
        parts.append(f"SUBTITLE: {event['subtitle']}")
    desc = event.get("description") or event.get("rules") or ""
    if desc:
        parts.append(f"DESCRIPTION: {desc[:600]}")
    parts.append(f"CATEGORY: {event.get('category', '?')}")
    parts.append(f"CLOSE TIME: {event.get('close_time', '?')}")
    if outs:
        parts.append(f"OUTCOMES ({n}): {', '.join(outs)}")
        parts.append(f"YES CONDITION: resolved_outcome == \"{yes_outcome}\"")
        parts.append(f"UNINFORMED PRIOR: 1/{n} = {1.0 / n:.3f}")
    parts.append(
        "\nWhat is the probability the first listed outcome is the winner? "
        'Respond as JSON: {"p_yes": <float>, "rationale": "<string>"}'
    )
    return "\n".join(parts)


def _parse_json(text: str) -> dict:
    """Extract p_yes/rationale from model output. Direct → embedded → regex."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{[^{}]*"p_yes"\s*:\s*[\d.]+[^{}]*\}', s)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    m = re.search(r'"?p_yes"?\s*[:=]\s*([\d.]+)', s)
    if m:
        return {"p_yes": float(m.group(1)), "rationale": text[:200]}
    raise ValueError(f"unparseable model output: {text[:200]}")


_anthropic_client = None


def _client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Put it in .env or export it.",
            )
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


def predict_single_llm(event: dict) -> dict:
    """One Anthropic call per event. Falls back to uniform prior on error."""
    try:
        resp = _client().messages.create(
            model=_FORECAST_MODEL,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(event)}],
            temperature=0.2,
        )
        text = resp.content[0].text if resp.content else ""
        parsed = _parse_json(text)
        p = _clamp(float(parsed["p_yes"]))
        return {
            "p_yes": p,
            "rationale": str(parsed.get("rationale", ""))[:300],
        }
    except Exception as e:
        log.warning(
            "single_llm fallback to uniform prior for %s: %s",
            event.get("market_ticker", "?"), e,
        )
        return predict_uniform_prior(event)


# CLI's --local expects a function named `predict`. We default to single_llm;
# swap aliases below for batch-comparing variants.
predict = predict_single_llm


__all__ = [
    "predict",
    "predict_uniform_prior",
    "predict_single_llm",
    "P_YES_MIN",
    "P_YES_MAX",
]
