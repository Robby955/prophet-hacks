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
- predict_uniform_prior:      deterministic 1/len(outcomes), clamped. No LLM cost.
- predict_single_llm:         one Anthropic Sonnet 4.6 call.
- predict_opus_47:            one Anthropic Opus 4.7 call (stronger reasoner).
- predict_ensemble_logit:     Sonnet 4.6 + GPT-5.5-pro, logit-mean blend.

All return ``{"p_yes": float ∈ [0.01, 0.99], "rationale": str}``.
"""
from __future__ import annotations

import json
import logging
import math
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
_OPUS_MODEL: str = os.environ.get(
    "PROPHET_FORECAST_OPUS_MODEL", "claude-opus-4-7",
)
# Claude Opus 4.6 leads the Prophet Arena "Default Harness" leaderboard at
# 0.9438 (per 2026-05-16 snapshot). Newer 4.7 is unproven on this benchmark
# yet -- worth a head-to-head.
_OPUS_46_MODEL: str = os.environ.get(
    "PROPHET_FORECAST_OPUS46_MODEL", "claude-opus-4-6",
)
_OPENAI_FORECAST_MODEL: str = os.environ.get(
    "PROPHET_FORECAST_OPENAI_MODEL", "gpt-5.5",
)
# GPT-5.2 is the top OpenAI fixed-context model on the leaderboard
# (0.9134); gpt-5.5 is newer but unranked.
_GPT52_MODEL: str = os.environ.get(
    "PROPHET_FORECAST_GPT52_MODEL", "gpt-5.2",
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
_openai_client = None


def _aclient():
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


def _oclient():
    global _openai_client
    if _openai_client is None:
        import openai
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Put it in .env or export it.",
            )
        _openai_client = openai.OpenAI(api_key=api_key)
    return _openai_client


def _call_anthropic(model: str, system: str, user: str) -> str:
    """Call Anthropic. Note: Opus 4.7 rejects `temperature` (deprecated for
    that model), so we omit it -- the model default is fine for forecasting.
    """
    resp = _aclient().messages.create(
        model=model,
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text if resp.content else ""


def _call_openai(model: str, system: str, user: str) -> str:
    """Call OpenAI. Note: GPT-5.x models reject `max_tokens` -- must use
    `max_completion_tokens`. They also reject `temperature` overrides
    (default 1.0 only), so we omit it. And: they consume tokens on internal
    reasoning before the visible response, so the cap must be generous
    enough that reasoning_tokens + visible_tokens fit. 300 leaves 0 for
    output; 2000 is comfortable for our short JSON contract.
    """
    resp = _oclient().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_completion_tokens=2000,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


def _predict_one_model(event: dict, *, model: str, vendor: str) -> dict:
    """Generic single-model predict that returns the standard contract."""
    user = _build_user_prompt(event)
    try:
        if vendor == "anthropic":
            text = _call_anthropic(model, SYSTEM_PROMPT, user)
        elif vendor == "openai":
            text = _call_openai(model, SYSTEM_PROMPT, user)
        else:
            raise ValueError(f"unknown vendor: {vendor}")
        parsed = _parse_json(text)
        return {
            "p_yes": _clamp(float(parsed["p_yes"])),
            "rationale": str(parsed.get("rationale", ""))[:300],
        }
    except Exception as e:
        log.warning(
            "model %s/%s fallback to uniform prior for %s: %s",
            vendor, model, event.get("market_ticker", "?"), e,
        )
        return predict_uniform_prior(event)


def predict_single_llm(event: dict) -> dict:
    """One Anthropic Sonnet 4.6 call per event."""
    return _predict_one_model(event, model=_FORECAST_MODEL, vendor="anthropic")


def predict_opus_47(event: dict) -> dict:
    """One Anthropic Opus 4.7 call per event. Stronger reasoner, ~3x cost."""
    return _predict_one_model(event, model=_OPUS_MODEL, vendor="anthropic")


def predict_opus_46(event: dict) -> dict:
    """One Anthropic Opus 4.6 call per event. The proven leaderboard top
    agent (0.9438 on Default Harness) -- known to perform on this benchmark.
    """
    return _predict_one_model(event, model=_OPUS_46_MODEL, vendor="anthropic")


def predict_gpt55(event: dict) -> dict:
    """One OpenAI GPT-5.5 call per event. Cross-vendor diversity."""
    return _predict_one_model(
        event, model=_OPENAI_FORECAST_MODEL, vendor="openai",
    )


def predict_gpt52(event: dict) -> dict:
    """One OpenAI GPT-5.2 call per event. The top OpenAI fixed-context
    leaderboard model (0.9134) -- proven on this benchmark.
    """
    return _predict_one_model(event, model=_GPT52_MODEL, vendor="openai")


def _logit(p: float) -> float:
    p = _clamp(p)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def predict_ensemble_logit(event: dict) -> dict:
    """Cross-vendor ensemble: Sonnet 4.6 + GPT-5.5, blended in logit space.

    Logit-mean preserves probabilistic semantics better than naive average
    for asymmetric splits. Falls back to whichever model succeeded if the
    other errors.
    """
    a = _predict_one_model(event, model=_FORECAST_MODEL, vendor="anthropic")
    o = _predict_one_model(
        event, model=_OPENAI_FORECAST_MODEL, vendor="openai",
    )
    pa, po = float(a["p_yes"]), float(o["p_yes"])
    blended = _sigmoid((_logit(pa) + _logit(po)) / 2.0)
    return {
        "p_yes": _clamp(blended),
        "rationale": (
            f"ensemble logit-mean(p_anthropic={pa:.3f}, p_openai={po:.3f}) = {blended:.3f}. "
            f"A: {a['rationale'][:100]} | O: {o['rationale'][:100]}"
        )[:300],
    }


def predict_ensemble_leaderboard(event: dict) -> dict:
    """Three-way ensemble of leaderboard-proven models: Opus 4.6 + GPT-5.2 +
    Sonnet 4.6. Logit-mean across all three. The Sonnet "anchor" gives us
    a cheap reliable read; Opus 4.6 contributes the strongest agent-track
    signal; GPT-5.2 contributes cross-vendor independence.
    """
    a1 = _predict_one_model(event, model=_FORECAST_MODEL, vendor="anthropic")
    a2 = _predict_one_model(event, model=_OPUS_46_MODEL, vendor="anthropic")
    o1 = _predict_one_model(event, model=_GPT52_MODEL, vendor="openai")
    ps = [float(a1["p_yes"]), float(a2["p_yes"]), float(o1["p_yes"])]
    blended = _sigmoid(sum(_logit(p) for p in ps) / len(ps))
    return {
        "p_yes": _clamp(blended),
        "rationale": (
            f"ensemble3 logit-mean(sonnet={ps[0]:.3f}, opus46={ps[1]:.3f}, "
            f"gpt52={ps[2]:.3f}) = {blended:.3f}. "
            f"S: {a1['rationale'][:60]} | O: {a2['rationale'][:60]} | "
            f"G: {o1['rationale'][:60]}"
        )[:300],
    }


# CLI's --local expects a function named `predict`. Default to the variant
# we want to ship; swap by reassigning here or by referencing the named
# variant from the CLI.
predict = predict_single_llm


__all__ = [
    "predict",
    "predict_uniform_prior",
    "predict_single_llm",
    "predict_opus_47",
    "predict_opus_46",
    "predict_gpt55",
    "predict_gpt52",
    "predict_ensemble_logit",
    "predict_ensemble_leaderboard",
    "P_YES_MIN",
    "P_YES_MAX",
]
