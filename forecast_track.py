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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from forecasting.borrowed_strength import (
    BorrowedStrengthInputs,
    borrowed_strength_estimate,
)


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


def longshot_guard_floor(n_outcomes: int) -> float:
    """Per-outcome probability floor per the Kalshi longshot bias finding.

    Kalshi paper (and Whelan writeup): buyers of <$0.10 contracts lose >60%
    on average. The PRINCIPLED floor from that empirical finding is 0.10,
    not "half the uniform prior."

    Previously this returned `max(0.05, 0.5 / n_outcomes)`, which for
    binary events (n=2) gave 0.25 -- forcing every binary prediction into
    [0.25, 0.75]. That's catastrophic when Opus 4.7 correctly anchors a
    longshot at 0.06 from cited market odds and the guard inflates it to
    0.25, destroying ~0.06 of Brier per event. Bug found 2026-05-16
    smoke-testing the Chiefs/SB-LXI synthetic.

    New rule: cap the floor at 0.10 (Kalshi threshold). The 0.5/n logic
    still narrows the floor on high-n events where the uniform prior is
    already below 0.10.

    For n=2  the floor is 0.10 (binary can go [0.10, 0.90]).
    For n=3  the floor is 0.10.
    For n=5  the floor is 0.10.
    For n=10 the floor is 0.05.
    For n=30 the floor is 0.05.
    """
    if n_outcomes <= 0:
        return 0.05
    return min(0.10, max(0.05, 0.5 / n_outcomes))


def apply_longshot_guard(
    probabilities: list[dict], n_outcomes: int,
) -> list[dict]:
    """Apply the longshot guard floor and renormalize.

    `probabilities` is a list of {"market": str, "probability": float}.
    Returns a NEW list (not in-place) with each probability raised to at
    least the floor, then the entire vector scaled so it sums to 1.0 if it
    was a valid distribution to begin with (sum within [0.5, 1.5]). If the
    sum is outside that band we don't renormalize -- the variant emitted
    something unusable and the server will normalize at its end.
    """
    if not probabilities:
        return probabilities
    floor = longshot_guard_floor(n_outcomes)
    floored = [
        {"market": p["market"], "probability": max(floor, float(p["probability"]))}
        for p in probabilities
    ]
    total = sum(p["probability"] for p in floored)
    if 0.5 <= total <= 1.5 and total > 0:
        # Renormalize but preserve the floor: shrink excess proportionally
        # from values ABOVE the floor only.
        excess = total - 1.0
        if abs(excess) < 1e-9:
            return floored
        above_floor = [p for p in floored if p["probability"] > floor + 1e-9]
        slack = sum(p["probability"] - floor for p in above_floor)
        if slack > 0 and excess > 0:
            # Trim excess from above-floor entries proportional to their slack
            scale = (slack - excess) / slack if slack > excess else 0.0
            for p in above_floor:
                p["probability"] = floor + (p["probability"] - floor) * max(0.0, scale)
        elif excess < 0:
            # Distribute the missing mass evenly across all outcomes
            add = -excess / len(floored)
            for p in floored:
                p["probability"] += add
    return floored


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


_COT_SYSTEM_PROMPT = """\
You are a calibrated probabilistic forecaster for binary prediction markets.

Your task: estimate the probability that the FIRST listed outcome
(outcomes[0]) is the resolved winner.

Follow this structured reasoning trace, then commit a final number. The
trace is required; outputting only p_yes without the trace is rejected.

Required JSON shape:
{
  "subgoals": ["<resolution criterion>", "<what would force YES>", "<what would force NO>"],
  "backward_check": "<work back from the close_time: what must be true?>",
  "p_initial": <float 0.01-0.99>,
  "verification": "<is p_initial consistent with base rates AND time-to-resolution? flag if not>",
  "p_yes": <float 0.01-0.99>,
  "rationale": "<1-2 sentence summary>"
}

Calibration scale:
  0.50 = no view; equal to the uninformed prior.
  0.60 = slight lean; weak base rate.
  0.70 = real view; concrete reasoning, multiple consistent signals.
  0.80 = strong view; hard evidence, clear mechanism.
  0.90 = near-certain; mechanically determined or authoritative source.

ABSTAIN RULE: If verification flags inconsistency or you have no
informational edge over the uninformed prior, p_yes MUST move toward the
uninformed prior (`1/len(outcomes)`), not away from it. Outputting near the
prior is the correct answer when uncertain -- it is not a failure.

Never emit 0.01 or 0.99 unless mechanically determined.
"""


def _build_cot_user_prompt(event: dict) -> str:
    """User prompt for the CoT variant. Adds an explicit abstain anchor."""
    yes_outcome = _yes_outcome(event)
    outs = event.get("outcomes") or []
    n = len(outs)
    prior = 1.0 / n if n else 0.5
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
        parts.append(
            f"UNINFORMED PRIOR: 1/{n} = {prior:.3f}. If you have no edge, "
            f"output p_yes within 0.02 of this prior."
        )
    parts.append(
        "\nReturn the full structured-CoT JSON. Do not skip any field."
    )
    return "\n".join(parts)


def _maybe_shrink_to_prior(
    p_model: float, rationale: str, prior: float,
    *, short_threshold_chars: int = 80, model_weight: float = 0.85,
) -> tuple[float, bool]:
    """If the model's rationale is short, shrink toward the prior.

    Low-conviction signal proxy: a rationale of <80 chars suggests the model
    didn't develop strong reasoning. Blend 85/15 model/prior in that case.
    Returns (p_blended, was_shrunk).
    """
    if len(rationale or "") < short_threshold_chars:
        blended = model_weight * p_model + (1.0 - model_weight) * prior
        return _clamp(blended), True
    return p_model, False


def _predict_one_model_cot(
    event: dict, *, model: str, vendor: str, shrink: bool = False,
) -> dict:
    """CoT-prompted single-model predict. Optional post-hoc shrinkage to prior."""
    user = _build_cot_user_prompt(event)
    outs = event.get("outcomes") or []
    prior = 1.0 / len(outs) if outs else 0.5
    try:
        if vendor == "anthropic":
            # Bigger token cap because CoT JSON has 6 fields, not 2.
            import anthropic  # noqa: F401
            resp = _aclient().messages.create(
                model=model, max_tokens=600,
                system=_COT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
            )
            text = resp.content[0].text if resp.content else ""
        elif vendor == "openai":
            resp = _oclient().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _COT_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                max_completion_tokens=3000,
                response_format={"type": "json_object"},
            )
            text = resp.choices[0].message.content or ""
        else:
            raise ValueError(f"unknown vendor: {vendor}")
        parsed = _parse_json(text)
        p_raw = _clamp(float(parsed["p_yes"]))
        rationale = str(parsed.get("rationale", ""))[:300]
        shrunk = False
        if shrink:
            p_raw, shrunk = _maybe_shrink_to_prior(p_raw, rationale, prior)
        return {
            "p_yes": p_raw,
            "rationale": (
                f"{rationale}"
                + (f" [shrunk toward prior {prior:.3f}]" if shrunk else "")
            )[:300],
        }
    except Exception as e:
        log.warning(
            "cot model %s/%s fallback to uniform prior for %s: %s",
            vendor, model, event.get("market_ticker", "?"), e,
        )
        return predict_uniform_prior(event)


def predict_sonnet_cot(event: dict) -> dict:
    """Sonnet 4.6 with structured CoT JSON prompt (no shrinkage).

    Tests change #1 from docs/research_notes.md (arxiv 2503.01307: structured
    reasoning with verification step). A/B against predict_single_llm.
    """
    return _predict_one_model_cot(
        event, model=_FORECAST_MODEL, vendor="anthropic", shrink=False,
    )


def predict_sonnet_cot_shrink(event: dict) -> dict:
    """Sonnet 4.6 + structured CoT + post-hoc shrinkage to prior.

    Combines change #1 and change #2 from docs/research_notes.md
    (CoT JSON + Scott Alexander's "abstain to mid" via shrinkage when
    rationale is short = low conviction proxy). A/B against
    predict_single_llm and predict_sonnet_cot.
    """
    return _predict_one_model_cot(
        event, model=_FORECAST_MODEL, vendor="anthropic", shrink=True,
    )


# ---------------------------------------------------------------------------
# Variant: multi-outcome direct elicitation (2026-05-16 server schema)
# ---------------------------------------------------------------------------

_MULTI_OUTCOME_SYSTEM_PROMPT = """\
You are a calibrated probabilistic forecaster for prediction markets.

Your task: assign a probability to EACH listed outcome of the event. Every
outcome must receive a probability; do not omit any. Your probabilities
for the outcomes should sum to approximately 1.

Calibration scale (apply to each outcome independently):
  0.50 = no view; default for genuine uncertainty.
  0.60 = slight lean; weak base rate or partial evidence.
  0.70 = real view; concrete reasoning, multiple consistent signals.
  0.80 = strong view; hard evidence, clear mechanism.
  0.90 = near-certain; mechanically determined or authoritative source.

Rules:
- Output ONLY valid JSON of the shape:
    {"probabilities": {"<outcome label>": <float>, ...},
     "rationale": "<one-line summary>"}
- Use the EXACT outcome labels supplied in the prompt as keys.
- Each probability must be in [0.01, 0.99].
- Never emit 0.01 or 0.99 unless mechanically determined.
- The rationale is a single line summarizing your overall reasoning.
"""


def _build_multi_outcome_user_prompt(event: dict) -> str:
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
        parts.append(f"OUTCOMES ({n}):")
        for o in outs:
            parts.append(f"  - {o}")
        parts.append(f"UNINFORMED PRIOR PER OUTCOME: 1/{n} = {1.0 / n:.3f}")
    parts.append(
        "\nReturn JSON of the shape "
        '{"probabilities": {"<outcome label>": <float>, ...}, '
        '"rationale": "<one-line summary>"}. '
        "Include every outcome label as a key, using the exact strings above."
    )
    return "\n".join(parts)


def _clean_loose_json(s: str) -> str:
    """Repair the JSON quirks our 2026-05-16 multi-vendor ablation exposed.

    Failure modes observed across Gemini 3.1 Pro, GPT-5.2, and Opus 4.6:
      - trailing commas before } or ]
      - smart-quote / unicode quote characters
      - leading prose before the JSON
      - explanation text after the closing brace
    """
    # Smart quotes -> straight
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    # Trailing commas (before } or ])
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return s


def _parse_multi_outcome_json(text: str) -> dict:
    """Extract {"probabilities": {...}, "rationale": "..."} from model output.

    Five-stage parse, robust against the JSON quirks the 2026-05-16
    multi-vendor ablation surfaced (trailing commas, smart quotes,
    prose before/after the JSON). Stages, in increasing leniency:

      0. Strip ``` code fences, json.loads as-is.
      1. _clean_loose_json then json.loads as-is.
      2. Regex find a JSON object containing "probabilities", parse it.
      3. Regex find a JSON object containing "probabilities" after
         _clean_loose_json, parse it.
      4. Regex find just the inner probabilities map and return it
         standalone (rationale becomes the first 200 chars).

    Stage 4 is the safety net: even if the surrounding JSON is broken,
    we can usually still pull `"probabilities": {...}` out as long as
    that one object is well-formed (after trailing-comma cleanup).
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]

    # Stage 0: direct
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Stage 1: clean common quirks, retry full parse
    cleaned = _clean_loose_json(s)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Stage 2: find a JSON object containing "probabilities"
    m = re.search(r'\{[\s\S]*?"probabilities"\s*:\s*\{[\s\S]*?\}[\s\S]*?\}', s)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # Stage 3: same regex, on the cleaned text
    m = re.search(r'\{[\s\S]*?"probabilities"\s*:\s*\{[\s\S]*?\}[\s\S]*?\}', cleaned)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # Stage 4: just the inner probabilities map, with trailing-comma cleanup
    m = re.search(r'"probabilities"\s*:\s*(\{[^{}]*\})', cleaned)
    if m:
        inner = _clean_loose_json(m.group(1))
        try:
            return {
                "probabilities": json.loads(inner),
                "rationale": text[:200],
            }
        except json.JSONDecodeError:
            pass

    raise ValueError(f"unparseable multi-outcome model output: {text[:200]}")


_BINARY_QUESTION_RE = re.compile(
    r"^\s*(will|does|is|has|have|had|are|was|were|can|could|should|would|do|did|may|might)\s",
    re.IGNORECASE,
)
_HAIKU_MODEL = os.environ.get("PROPHET_INFERENCE_MODEL", "claude-haiku-4-5-20251001")


def _infer_outcomes_when_missing(event: dict) -> list[str]:
    """Real safety net: if PA's /predict webhook sends an event WITHOUT
    `outcomes`, our pipeline would return `probabilities: []` -- worst
    possible Brier. Investigation 2026-05-16 showed PA's /forecast/events
    endpoint returns outcomes=[] for all 18 closed events it exposes,
    so the live webhook shape is uncertain.

    Strategy when outcomes is missing or empty:
      1. If title matches a binary question pattern ("Will X?", "Does Y?",
         etc.), return ["Yes", "No"]. Covers most prediction-market shapes.
      2. Else, fire a cheap Haiku 4.5 call to infer 2-6 plausible outcomes.
      3. Last resort: ["Yes", "No"].

    Always logs a WARNING when inference fires, so we know in production
    if PA is sending the light shape. Caller should flag in the response
    metadata so /predictions can show that inference happened.
    """
    existing = event.get("outcomes") or []
    if existing:
        return list(existing)

    title = (event.get("title") or "").strip()
    if not title:
        return []

    log.warning(
        "outcomes missing for %s; inferring from title=%r",
        event.get("market_ticker", "?"), title[:80],
    )

    if _BINARY_QUESTION_RE.match(title):
        return ["Yes", "No"]

    try:
        prompt = (
            f"Question: {title}\n"
            f"Category: {event.get('category','')}\n"
            f"Rules: {(event.get('rules') or '')[:300]}\n\n"
            "Return ONLY a JSON array of 2-6 plausible distinct outcome "
            "strings. No prose. No commentary. Example shapes:\n"
            '  ["Yes", "No"]\n'
            '  ["Democrat", "Republican", "Third party"]\n'
            '  ["Kansas City Chiefs", "Philadelphia Eagles", "Other"]'
        )
        resp = _aclient().messages.create(
            model=_HAIKU_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip() if resp.content else ""
        m = re.search(r"\[\s*\"[^\[\]]*?\"\s*(?:,\s*\"[^\[\]]*?\"\s*)*\]", text, re.DOTALL)
        if m:
            arr = json.loads(m.group())
            if isinstance(arr, list) and 2 <= len(arr) <= 10:
                clean = [str(o).strip() for o in arr if str(o).strip()]
                if 2 <= len(clean) <= 10:
                    return clean
    except Exception as e:
        log.warning("Haiku outcome inference failed: %s", e)

    return ["Yes", "No"]


def _match_outcome_label(raw_key: str, candidates: list[str]) -> str | None:
    """Resolve a raw model-emitted key to the closest candidate outcome label.

    Models sometimes return slightly different casing or whitespace than
    the canonical outcome labels (e.g. "yes" vs "Yes", " Lakers " vs "Lakers").
    The 2026-05-16 multi-vendor ablation showed this is a real failure mode;
    when probabilities assigned to unrecognized keys, our caller fell back
    to uniform prior and lost the event.

    Strategy: exact match -> case-insensitive -> stripped -> normalized
    (collapse whitespace + strip non-alphanumeric). Return None if no
    candidate within those four passes is unique; caller treats as miss
    and uses prior. We do NOT do Levenshtein/fuzzy substring -- those
    risk silently mapping the wrong outcome.
    """
    if not raw_key:
        return None
    if raw_key in candidates:
        return raw_key
    raw_low = raw_key.lower()
    lows = [c.lower() for c in candidates]
    if raw_low in lows:
        return candidates[lows.index(raw_low)]
    raw_str = raw_low.strip()
    strs = [c.lower().strip() for c in candidates]
    if raw_str in strs and strs.count(raw_str) == 1:
        return candidates[strs.index(raw_str)]
    norm = re.sub(r"[^a-z0-9]", "", raw_str)
    norms = [re.sub(r"[^a-z0-9]", "", c.lower()) for c in candidates]
    if norm and norm in norms and norms.count(norm) == 1:
        return candidates[norms.index(norm)]
    return None


def predict_multi_outcome(event: dict) -> dict:
    """One Anthropic Sonnet 4.6 call that emits per-outcome probabilities.

    Aligned with the 2026-05-16 Prophet Arena server schema, which expects
    a `probabilities` list of `{market, probability}` pairs (one per
    outcome). The model assigns a probability to EACH outcome directly --
    no single-`p_yes` distribution step needed.

    Returns the standard variant contract plus a NEW `probabilities` list:
        {
            "p_yes": <prob assigned to outcomes[0]>,
            "rationale": "<one-line summary>",
            "probabilities": [
                {"market": "<outcome>", "probability": <float>},
                ...
            ],
        }

    Callers reading only `p_yes` keep working; callers that understand the
    new field (the live agent server) use it as authoritative per-outcome
    output. Probabilities do not have to sum to 1; the server normalizes.
    """
    outs = event.get("outcomes") or []
    if not outs:
        return {"p_yes": 0.5, "rationale": "no outcomes", "probabilities": []}
    user = _build_multi_outcome_user_prompt(event)
    try:
        # max_tokens=900 leaves comfortable room for 20-30 outcome JSON.
        resp = _aclient().messages.create(
            model=_FORECAST_MODEL,
            max_tokens=900,
            system=_MULTI_OUTCOME_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text if resp.content else ""
        parsed = _parse_multi_outcome_json(text)
        raw_probs = parsed.get("probabilities") or {}
        if not isinstance(raw_probs, dict):
            raise ValueError(
                f"probabilities not a dict: {type(raw_probs).__name__}",
            )
        # Build per-outcome list in input order; clamp each value. For any
        # outcome the model omitted, fall back to the uninformed prior.
        prior = 1.0 / len(outs)
        prob_list: list[dict] = []
        for o in outs:
            v = raw_probs.get(o)
            if v is None:
                p = prior
            else:
                try:
                    p = _clamp(float(v))
                except (TypeError, ValueError):
                    p = prior
            prob_list.append({"market": o, "probability": p})
        rationale = str(parsed.get("rationale", ""))[:300]
        # Kalshi longshot guard: floor at min(0.10, max(0.05, 0.5/n)) and renormalize.
        prob_list = apply_longshot_guard(prob_list, len(outs))
        return {
            "p_yes": prob_list[0]["probability"],
            "rationale": rationale,
            "probabilities": prob_list,
        }
    except Exception as e:
        log.warning(
            "multi_outcome model %s fallback to uniform prior for %s: %s",
            _FORECAST_MODEL, event.get("market_ticker", "?"), e,
        )
        base = predict_uniform_prior(event)
        p = float(base["p_yes"])
        return {
            "p_yes": p,
            "rationale": base["rationale"],
            "probabilities": [
                {"market": o, "probability": p} for o in outs
            ],
        }


def _self_consistency_multi_outcome(event: dict, k: int = 3) -> dict:
    """Run `predict_multi_outcome` k times in parallel and average per-outcome.

    Self-consistency: drawing k independent samples from the same model and
    averaging is a variance-reduction technique that tends to improve
    calibration on noisy events (arxiv 2203.11171). We fan out k=3 in
    parallel via ThreadPoolExecutor (the cached `_aclient()` is thread-safe)
    and average the per-outcome probabilities returned by each call.
    Rationales are concatenated and truncated to ~300 chars.

    Robustness:
    - If any of the k calls raises, that sample is dropped and the average
      is taken over the surviving samples.
    - If ALL k fail, falls back to `predict_uniform_prior` (and synthesizes
      a per-outcome probabilities list at the uniform prior).

    Cost: ~k * single-LLM ≈ ~$0.015/event for k=3 against Sonnet 4.6.
    Parallelism is capped at k -- we don't over-fan-out and trip rate
    limits.
    """
    outs = event.get("outcomes") or []
    if not outs:
        return {"p_yes": 0.5, "rationale": "no outcomes", "probabilities": []}
    if k < 1:
        k = 1

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=k) as ex:
        futures = [ex.submit(predict_multi_outcome, event) for _ in range(k)]
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as e:
                log.warning(
                    "self-consistency sample failed for %s: %s",
                    event.get("market_ticker", "?"), e,
                )
                continue
            if isinstance(r, dict):
                results.append(r)

    if not results:
        log.warning(
            "multi_outcome_sc%d all %d samples failed for %s; uniform fallback",
            k, k, event.get("market_ticker", "?"),
        )
        base = predict_uniform_prior(event)
        p = float(base["p_yes"])
        return {
            "p_yes": p,
            "rationale": base["rationale"],
            "probabilities": [
                {"market": o, "probability": p} for o in outs
            ],
        }

    # Average per-outcome probabilities, keyed by the original outcomes
    # ordering for output stability. For each outcome, sum probabilities
    # across the surviving samples (treating missing entries as the
    # uniform prior) and divide by the sample count.
    prior = 1.0 / len(outs)
    prob_list: list[dict] = []
    for o in outs:
        vals: list[float] = []
        for r in results:
            raw = r.get("probabilities") or []
            v: float | None = None
            if isinstance(raw, list):
                for entry in raw:
                    if (
                        isinstance(entry, dict)
                        and entry.get("market") == o
                        and "probability" in entry
                    ):
                        try:
                            v = float(entry["probability"])
                        except (TypeError, ValueError):
                            v = None
                        break
            vals.append(v if v is not None else prior)
        avg = sum(vals) / len(vals) if vals else prior
        prob_list.append({"market": o, "probability": _clamp(avg)})

    rationales = [
        str(r.get("rationale", "")) for r in results if r.get("rationale")
    ]
    combined = " | ".join(rationales)
    rationale = (f"sc{len(results)}: {combined}" if combined else f"sc{len(results)}")[:300]

    return {
        "p_yes": prob_list[0]["probability"],
        "rationale": rationale,
        "probabilities": prob_list,
    }


def predict_multi_outcome_sc3(event: dict) -> dict:
    """Self-consistency-3 variant: average k=3 parallel `predict_multi_outcome`
    samples per outcome.

    Reduces sampling variance vs. a single Sonnet 4.6 multi-outcome call;
    should help calibration on noisy/ambiguous events at the cost of ~3x
    the single-LLM spend (~$0.015/event). Drops failed samples; if all
    fail, returns `predict_uniform_prior`.
    """
    return _self_consistency_multi_outcome(event, k=3)


# ---------------------------------------------------------------------------
# Variant: Brave-search retrieval-augmented multi-outcome
# ---------------------------------------------------------------------------

_BRAVE_SEARCH_URL: str = "https://api.search.brave.com/res/v1/web/search"

# Priority list for dedupe: prefer official sources (.gov / .edu first --
# handled by suffix match), then exchanges-of-record, then top-tier news
# outlets. Anything not in this list is lowest priority.
_PRIORITY_DOMAINS: tuple[str, ...] = (
    "kalshi.com",
    "polymarket.com",
    "apnews.com",
    "reuters.com",
    "bbc.com",
    "npr.org",
)


def _domain_priority(domain: str) -> int:
    """Lower number = higher priority. .gov / .edu first, then the curated
    list in order, then everything else.
    """
    d = (domain or "").lower()
    if d.endswith(".gov"):
        return 0
    if d.endswith(".edu"):
        return 1
    for i, prio in enumerate(_PRIORITY_DOMAINS):
        if d == prio or d.endswith("." + prio):
            return 2 + i
    return 100


def _extract_domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except (ValueError, TypeError):
        return ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _brave_search(query: str, count: int = 5) -> list[dict]:
    """Hit the Brave Web Search API.

    Returns a list of `{"title", "url", "snippet", "domain"}` dicts (up to
    `count`). Raises `RuntimeError` if `BRAVE_SEARCH_API_KEY` is missing or
    the HTTP call fails.
    """
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not api_key:
        raise RuntimeError("BRAVE_SEARCH_API_KEY not set")
    try:
        r = httpx.get(
            _BRAVE_SEARCH_URL,
            params={"q": query, "count": count},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            timeout=10.0,
        )
        r.raise_for_status()
    except (httpx.HTTPError, ValueError) as e:
        raise RuntimeError(f"Brave search failed: {e}") from e

    data = r.json() or {}
    raw = ((data.get("web") or {}).get("results") or [])
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url:
            continue
        out.append({
            "title": str(item.get("title") or "")[:200],
            "url": url,
            "snippet": str(item.get("description") or item.get("snippet") or "")[:400],
            "domain": _extract_domain(url),
        })
    return out


def _dedupe_by_domain(results: list[dict]) -> list[dict]:
    """Keep one result per unique domain, prefer official/curated sources,
    cap at 5 entries total.

    Two-pass: collapse by domain (keeping the first occurrence -- Brave's
    own ranking is the tie-breaker within a domain), then sort survivors by
    priority (lowest `_domain_priority` first), then take the top 5.
    """
    by_domain: dict[str, dict] = {}
    for r in results:
        d = r.get("domain") or ""
        if not d:
            continue
        by_domain.setdefault(d, r)
    ranked = sorted(
        by_domain.values(),
        key=lambda x: _domain_priority(x.get("domain") or ""),
    )
    return ranked[:5]


def _build_query(event: dict) -> str:
    """Build a Brave query from event title + the most informative outcome.

    "Most informative" = the longest outcome label that isn't a generic
    binary "Yes"/"No". Capped at 200 chars total.
    """
    title = str(event.get("title") or "").strip()
    outs = [str(o) for o in (event.get("outcomes") or [])]
    informative = [o for o in outs if o.strip().lower() not in {"yes", "no"}]
    pool = informative or outs
    pick = max(pool, key=len) if pool else ""
    if pick and pick.lower() not in title.lower():
        q = f"{title} {pick}".strip()
    else:
        q = title
    return q[:200]


_MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT = """\
You are a calibrated probabilistic forecaster for prediction markets with
access to recent web evidence.

Your task: assign a probability to EACH listed outcome of the event using
both your prior knowledge and the supplied evidence snippets. Every outcome
must receive a probability; do not omit any. Your probabilities for the
outcomes should sum to approximately 1.

Treat the evidence snippets as factual claims from third-party sources.
Do not fabricate URLs, dates, or details that are not present in the
snippets. If the evidence is stale or unrelated to the resolution
criterion, say so in the rationale and lean closer to the uninformed
prior.

Calibration scale (apply to each outcome independently):
  0.50 = no view; default for genuine uncertainty.
  0.60 = slight lean; weak base rate or partial evidence.
  0.70 = real view; concrete reasoning, multiple consistent signals.
  0.80 = strong view; hard evidence, clear mechanism.
  0.90 = near-certain; mechanically determined or authoritative source.

Rules:
- Output ONLY valid JSON of the shape:
    {"probabilities": {"<outcome label>": <float>, ...},
     "rationale": "<one-line summary>"}
- Use the EXACT outcome labels supplied in the prompt as keys.
- Each probability must be in [0.01, 0.99].
- Never emit 0.01 or 0.99 unless mechanically determined.
- The rationale is a single line summarizing your overall reasoning.
"""


def _build_retrieval_user_prompt(event: dict, chunks: list[dict]) -> str:
    """Multi-outcome prompt enriched with Brave evidence snippets.

    URLs are NOT included in the prompt -- they live in the audit trail
    (`evidence_urls`) so the model can't quote them as primary sources.
    """
    base = _build_multi_outcome_user_prompt(event)
    if not chunks:
        return base
    parts = [base, "", "Recent evidence (do not invent details):"]
    for i, c in enumerate(chunks, 1):
        title = (c.get("title") or "").strip()
        snippet = (c.get("snippet") or "").strip()
        if title and snippet:
            parts.append(f"[{i}] {title} -- {snippet}")
        elif title:
            parts.append(f"[{i}] {title}")
        elif snippet:
            parts.append(f"[{i}] {snippet}")
    return "\n".join(parts)


def _hours_to_resolution(event: dict) -> float:
    raw = str(event.get("close_time") or "").strip()
    if not raw:
        return 48.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta_hours = (dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        # Historical backtests contain already-closed events. Treat those as
        # neutral medium-horizon forecasts rather than "resolves immediately",
        # otherwise the shrinkage gate would be artificially pessimistic.
        return delta_hours if delta_hours > 0 else 48.0
    except ValueError:
        return 48.0


def _event_domain(event: dict) -> str:
    raw = str(event.get("category") or "other").lower().strip()
    raw = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return raw or "other"


def _sae_source_quality(chunks: list[dict]) -> float:
    if not chunks:
        return 0.35
    best_priority = min(_domain_priority(c.get("domain") or "") for c in chunks)
    if best_priority <= 1:
        return 0.85
    if best_priority <= 8:
        return 0.75
    return min(0.70, 0.45 + 0.05 * len(chunks))


def _apply_sae_borrowed_strength(
    probabilities: list[dict],
    *,
    event: dict,
    chunks: list[dict],
) -> tuple[list[dict], dict]:
    """Shrink raw per-outcome model probabilities toward 1/n.

    Prophet's /predict payload does not include live market prices, so this
    offline experimental variant deliberately uses the uninformed prior as
    `p_market` and keeps the auditable borrowed-strength pieces that do not
    require market data: source quality, horizon, domain reliability, and
    model uncertainty. Do not parse odds out of snippets here.
    """
    if not probabilities:
        return probabilities, {"applied": False, "reason": "no probabilities"}

    n = len(probabilities)
    prior = 1.0 / n
    domain = _event_domain(event)
    hours = _hours_to_resolution(event)
    source_quality = _sae_source_quality(chunks)
    retrieval_conflict = 0.0 if chunks else 0.30
    spread = min(0.20, max(0.02, prior * 0.20))
    yes_bid = max(0.01, prior - spread)
    yes_ask = min(0.99, prior + spread)

    shrunk: list[dict] = []
    audit_rows: list[dict] = []
    for p in probabilities:
        market = str(p.get("market") or "")
        try:
            raw_p = _clamp(float(p.get("probability", prior)))
        except (TypeError, ValueError):
            raw_p = prior
        result = borrowed_strength_estimate(
            BorrowedStrengthInputs(
                p_market=prior,
                p_models=[(_OPUS_MODEL, raw_p)],
                domain=domain,
                source_quality=source_quality,
                retrieval_conflict=retrieval_conflict,
                hours_to_resolution=hours,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                p_history=None,
                n_history=0,
            ),
        )
        shrunk.append({"market": market, "probability": result.p_final})
        audit_rows.append({
            "market": market,
            "raw_probability": round(raw_p, 6),
            "shrunk_probability": round(result.p_final, 6),
            "action": result.action,
            "weights": result.weights,
            "uncertainty": {
                "aggregate": round(result.uncertainty.aggregate, 6),
                "model_disagreement": round(result.uncertainty.model_disagreement, 6),
                "retrieval_conflict": round(result.uncertainty.retrieval_conflict, 6),
                "spread_width": round(result.uncertainty.spread_width, 6),
                "horizon_proximity": round(result.uncertainty.horizon_proximity, 6),
            },
            "decisions": result.decisions,
        })

    return shrunk, {
        "applied": True,
        "p_market_source": "uninformed_prior_1_over_n",
        "p_market": round(prior, 6),
        "domain": domain,
        "hours_to_resolution": round(hours, 3),
        "source_quality": round(source_quality, 3),
        "retrieval_conflict": round(retrieval_conflict, 3),
        "outcomes": audit_rows,
    }


def _predict_multi_outcome_retrieval_impl(event: dict, *, apply_sae: bool = False) -> dict:
    """Brave-search retrieval-augmented multi-outcome forecaster.

    Pipeline:
      1. Build a search query from event title + most-informative outcome.
      2. Hit Brave Search (count=5).
      3. Dedupe by domain, preferring official sources (.gov/.edu, then a
         curated list of exchanges and major outlets), max 5 chunks.
      4. Run an Opus 4.7 multi-outcome call with the evidence injected as
         "Recent evidence (do not invent details): ..." -- titles and
         snippets only; URLs are kept in `evidence_urls` for audit.
      5. Apply the Kalshi longshot guard -- every per-outcome probability
         floored at `min(0.10, max(0.05, 0.5/n_outcomes))`.

    Fallbacks:
      - If `BRAVE_SEARCH_API_KEY` is unset OR the Brave call fails, log a
        warning and fall back to `predict_multi_outcome` (longshot guard
        still applied). `evidence_urls` is empty.
      - If the LLM call fails, falls back to uniform prior with the
        longshot guard applied.

    Cost: ~$0.08-0.12 per event (Opus 4.7 input/output on a ~5500-token
    enriched prompt + ~500-token JSON response); Brave free tier covers
    2k searches/month. Sonnet 4.6 fallback exists if the Opus call fails.

    Returns:
        {
            "p_yes": <prob of outcomes[0]>,
            "rationale": "<one-line summary>",
            "probabilities": [
                {"market": "<outcome>", "probability": <float>}, ...
            ],
            "evidence_urls": [<str>, ...],
        }
    """
    import time as _time  # local import to keep module import surface stable
    t_start = _time.time()
    trace: dict = {
        "brave_query": None,
        "brave_n_results": 0,
        "chunks": [],
        "forecast_model": None,
        "raw_llm_text": None,
        "parse_path": None,
        "fuzzy_matches": [],
        "outcomes_inferred": False,
        "longshot_guard_applied": False,
        "latency_ms": {},
        "warnings": [],
    }
    if apply_sae:
        trace["sae"] = {"applied": False, "reason": "not reached"}

    # Safety net: PA's /predict webhook *may* send events without outcomes.
    outcomes_inferred = not bool(event.get("outcomes"))
    trace["outcomes_inferred"] = outcomes_inferred
    outs = _infer_outcomes_when_missing(event)
    n = len(outs)
    if n == 0:
        trace["warnings"].append("no outcomes and no title to infer from")
        trace["latency_ms"]["total"] = int((_time.time() - t_start) * 1000)
        return {
            "p_yes": 0.5,
            "rationale": "no outcomes and no title to infer from",
            "probabilities": [],
            "evidence_urls": [],
            "_trace": trace,
        }
    if outcomes_inferred:
        event = {**event, "outcomes": outs}

    # ---- 1-3. Retrieve evidence (degrade gracefully). ----
    if not os.environ.get("BRAVE_SEARCH_API_KEY"):
        log.warning(
            "BRAVE_SEARCH_API_KEY missing; %s falling back to predict_multi_outcome",
            event.get("market_ticker", "?"),
        )
        print(json.dumps({
            "event": "retrieval_degraded",
            "reason": "brave_key_missing",
            "market_ticker": event.get("market_ticker"),
            "task_id": task_id if "task_id" in locals() else None,
        }), flush=True)
        trace["warnings"].append("BRAVE_SEARCH_API_KEY missing; fell through to predict_multi_outcome")
        base = predict_multi_outcome(event)
        guarded = apply_longshot_guard(base.get("probabilities", []), n)
        trace["longshot_guard_applied"] = True
        trace["latency_ms"]["total"] = int((_time.time() - t_start) * 1000)
        return {
            "p_yes": guarded[0]["probability"] if guarded else 0.5,
            "rationale": base.get("rationale", "")[:300],
            "probabilities": guarded,
            "evidence_urls": [],
            "_trace": trace,
        }

    chunks: list[dict] = []
    query = _build_query(event)
    trace["brave_query"] = query
    t_brave = _time.time()
    try:
        raw = _brave_search(query, count=5)
        chunks = _dedupe_by_domain(raw)
        trace["brave_n_results"] = len(raw) if raw else 0
    except RuntimeError as e:
        log.warning(
            "Brave search failed for %s: %s -- continuing without evidence",
            event.get("market_ticker", "?"), e,
        )
        print(json.dumps({
            "event": "retrieval_degraded",
            "reason": f"brave_search_failed: {str(e)[:160]}",
            "market_ticker": event.get("market_ticker"),
            "task_id": task_id if "task_id" in locals() else None,
        }), flush=True)
        trace["warnings"].append(f"brave search failed: {str(e)[:120]}")
        chunks = []
    trace["latency_ms"]["brave"] = int((_time.time() - t_brave) * 1000)
    trace["chunks"] = [
        {
            "title": (c.get("title") or "")[:140],
            "snippet": (c.get("snippet") or "")[:200],
            "url": c.get("url") or "",
            "host": (c.get("url") or "").split("/")[2] if "://" in (c.get("url") or "") else "",
        }
        for c in chunks[:5]
    ]
    evidence_urls = [c["url"] for c in chunks if c.get("url")]

    # ---- 4. Multi-outcome LLM call with evidence-enriched prompt. ----
    user = _build_retrieval_user_prompt(event, chunks)
    forecast_model = _OPUS_MODEL
    trace["forecast_model"] = forecast_model
    t_llm = _time.time()
    try:
        resp = _aclient().messages.create(
            model=forecast_model,
            max_tokens=900,
            system=_MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text if resp.content else ""
        trace["raw_llm_text"] = text[:1500]
        parsed = _parse_multi_outcome_json(text)
        trace["parse_path"] = parsed.get("_parse_path") or "direct"
        raw_probs = parsed.get("probabilities") or {}
        if not isinstance(raw_probs, dict):
            raise ValueError(
                f"probabilities not a dict: {type(raw_probs).__name__}",
            )
        prior = 1.0 / n
        resolved_probs: dict[str, float] = {}
        for raw_key, raw_val in raw_probs.items():
            canonical = _match_outcome_label(str(raw_key), outs)
            if canonical is None:
                trace["fuzzy_matches"].append({"raw": str(raw_key)[:60], "matched": None})
                continue
            if canonical in resolved_probs:
                continue
            if canonical != str(raw_key):
                trace["fuzzy_matches"].append({"raw": str(raw_key)[:60], "matched": canonical[:60]})
            try:
                resolved_probs[canonical] = _clamp(float(raw_val))
            except (TypeError, ValueError):
                trace["warnings"].append(f"non-numeric probability for {canonical[:30]!r}: {raw_val!r}")
        fallback = prior if n <= 2 else min(prior, longshot_guard_floor(n))
        prob_list: list[dict] = []
        for o in outs:
            p = resolved_probs.get(o, fallback)
            prob_list.append({"market": o, "probability": p})
        rationale = str(parsed.get("rationale", ""))[:300]
    except Exception as e:
        log.warning(
            "multi_outcome_retrieval LLM call failed for %s: %s; uniform fallback",
            event.get("market_ticker", "?"), e,
        )
        trace["warnings"].append(f"llm call failed: {str(e)[:160]}")
        prior = 1.0 / n
        prob_list = [{"market": o, "probability": prior} for o in outs]
        rationale = f"llm error: {e}"
    trace["latency_ms"]["llm"] = int((_time.time() - t_llm) * 1000)

    # ---- 5. Apply Kalshi longshot guard (always). ----
    if apply_sae:
        prob_list, sae_trace = _apply_sae_borrowed_strength(
            prob_list,
            event=event,
            chunks=chunks,
        )
        trace["sae"] = sae_trace
        rationale = f"sae shrinkage toward 1/{n}: {rationale}"[:300]

    guarded = apply_longshot_guard(prob_list, n)
    trace["longshot_guard_applied"] = True
    trace["latency_ms"]["total"] = int((_time.time() - t_start) * 1000)
    out = {
        "p_yes": guarded[0]["probability"] if guarded else 0.5,
        "rationale": rationale,
        "_trace": trace,
        "probabilities": guarded,
        "evidence_urls": evidence_urls,
    }
    if apply_sae:
        out["sae"] = trace.get("sae")
    return out


def predict_multi_outcome_retrieval(event: dict) -> dict:
    return _predict_multi_outcome_retrieval_impl(event, apply_sae=False)


def predict_multi_outcome_retrieval_sae(event: dict) -> dict:
    """Offline experimental retrieval variant with SAE shrinkage.

    This does not change production routing. It uses the exact retrieval +
    Opus 4.7 parse path, then shrinks each raw outcome probability toward
    the uninformed 1/n prior using `borrowed_strength_estimate`.
    """
    return _predict_multi_outcome_retrieval_impl(event, apply_sae=True)


def predict_hybrid_routed(event: dict) -> dict:
    """Route by outcome count: binary -> gpt55, multi-outcome -> multi_outcome.

    Motivation (from the 26-event calibration report, see
    docs/reports/calibration_summary.md): aggregate per-outcome Brier puts
    gpt55 at 0.670 (best) while multi_outcome lands at 0.699 (mid-pack),
    BUT on the 12 multi-outcome events alone multi_outcome scores 0.84 vs
    uniform 0.91 -- it pays off where designed. The aggregate is dragged
    up by 16 binary sports matchups where leaning hard off 0.5 hurt the
    multi-outcome prompt. This hybrid plays each strength to its category:

      n <= 2 outcomes (binary): predict_gpt55 (best aggregate)
      n  > 2 outcomes (multi):  predict_multi_outcome (designed for this)

    For consistency we still emit the per-outcome `probabilities` shape on
    both branches: gpt55 returns binary p_yes only, so we distribute it
    across outcomes the way the server endpoint does for legacy variants.
    """
    outs = event.get("outcomes") or []
    n = len(outs)
    if n <= 2:
        # Binary path: gpt55 emits p_yes only; synthesize multi-outcome
        # probabilities by giving outcomes[0] p_yes and the other (if any)
        # the remainder. This matches what the server endpoint does for
        # legacy binary variants.
        base = predict_gpt55(event)
        p = float(base["p_yes"])
        probs: list[dict] = []
        if n == 0:
            probs = []
        elif n == 1:
            probs = [{"market": outs[0], "probability": 1.0}]
        else:
            probs = [
                {"market": outs[0], "probability": p},
                {"market": outs[1], "probability": max(0.0, 1.0 - p)},
            ]
        # Kalshi longshot guard: for n=2 the floor is 0.10, so binary
        # predictions may still express real longshots.
        probs = apply_longshot_guard(probs, n)
        return {
            "p_yes": probs[0]["probability"] if probs else p,
            "rationale": f"hybrid(binary->gpt55+guard): {base.get('rationale','')[:240]}",
            "probabilities": probs,
        }
    # Multi-outcome path
    base = predict_multi_outcome(event)
    rat = base.get("rationale", "")
    return {
        "p_yes": float(base["p_yes"]),
        "rationale": f"hybrid(multi->multi_outcome): {rat[:240]}",
        "probabilities": base.get("probabilities", []),
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
    "predict_hybrid_routed",
    "predict_sonnet_cot",
    "predict_sonnet_cot_shrink",
    "predict_multi_outcome",
    "predict_multi_outcome_sc3",
    "predict_multi_outcome_retrieval",
    "predict_multi_outcome_retrieval_sae",
    "P_YES_MIN",
    "P_YES_MAX",
]
