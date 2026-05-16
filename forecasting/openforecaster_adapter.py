"""OpenForecaster / Eternis-Forecaster-8B adapter — OPTIONAL auxiliary expert.

Per Rob's locked rule (2026-05-15): do NOT replace frontier models
with an 8B specialized forecaster unless it beats them on OUR
validation set. Use it as a cheap auxiliary ensemble member only if
it earns its weight via the Hedge expert pool's running Brier.

This file is a stub adapter. It does not download or run any model
by default. Live use requires:

    PROPHET_OPENFORECASTER_ENABLED=1
    PROPHET_OPENFORECASTER_BACKEND=runpod|local|api
    PROPHET_OPENFORECASTER_ENDPOINT=https://...   # if backend=api
    PROPHET_OPENFORECASTER_MODEL=Eternis-Forecaster-8B  # or OpenForecaster

The adapter never fine-tunes anything. It only calls a pretrained
model with our canonical decomposition prompt and parses the output
into our canonical JSON schema (forecasting.prompts).

References (verify the URLs / model availability before relying on them):
- Eternis-Forecaster-8B announcement (Eternis blog, 2025).
- OpenForecaster paper (arXiv): generated ~50K open-ended questions from
  news, used offline static snapshots to avoid leakage, retrieval +
  GRPO fine-tuning of Qwen3-thinking. Accuracy + Brier composite
  outperformed either alone.
- OpenForesight benchmark dataset.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


ENABLED_FLAG = "PROPHET_OPENFORECASTER_ENABLED"
BACKEND_VAR = "PROPHET_OPENFORECASTER_BACKEND"
ENDPOINT_VAR = "PROPHET_OPENFORECASTER_ENDPOINT"
MODEL_VAR = "PROPHET_OPENFORECASTER_MODEL"


@dataclass(frozen=True)
class OpenForecasterResult:
    p_yes: float
    rationale: str
    raw: dict
    model_name: str
    backend: str


def is_enabled() -> bool:
    return os.getenv(ENABLED_FLAG, "").strip() in ("1", "true", "yes")


def backend() -> str:
    return os.getenv(BACKEND_VAR, "api")


def model_name() -> str:
    return os.getenv(MODEL_VAR, "Eternis-Forecaster-8B")


def smoke_check() -> dict:
    """Cheap, no-network health check. Confirms the env wiring is sane."""
    return {
        "enabled": is_enabled(),
        "backend": backend(),
        "model": model_name(),
        "has_endpoint": bool(os.getenv(ENDPOINT_VAR)),
    }


def forecast(prompt: str) -> Optional[OpenForecasterResult]:
    """Call the OpenForecaster/EF-8B model with a canonical prompt.

    Returns None when disabled. Live implementation must:
      - parse strict JSON output per `forecasting.prompts.REQUIRED_FIELDS`
      - never let the model decide trade size
      - log the call in the JSONL trace as `model_name=openforecaster_*`
        so the expert_pool can track its running Brier and weight
        accordingly
    """
    if not is_enabled():
        return None
    raise NotImplementedError(
        "OpenForecaster/EF-8B adapter not yet wired. "
        "Set PROPHET_OPENFORECASTER_ENABLED=1 + PROPHET_OPENFORECASTER_BACKEND "
        "and wire the actual HTTP client in this file before the live run."
    )


# -- Eligibility / registration rules ------------------------------------


def should_register_in_expert_pool(
    *,
    holdout_brier: float,
    holdout_n: int,
    frontier_holdout_brier: float,
    min_holdout: int = 50,
    must_beat_margin: float = 0.005,
) -> bool:
    """Decide whether to add this model to the live expert pool.

    Locked rule: do NOT add OpenForecaster to the live ensemble unless
    it has at least `min_holdout` resolved holdout outcomes AND beats
    the best frontier-model expert on Brier by `must_beat_margin`.

    Default 0.005 absolute Brier beat is roughly the noise floor on
    100-event holdout sets. Conservative on purpose.
    """
    if holdout_n < min_holdout:
        return False
    return holdout_brier <= (frontier_holdout_brier - must_beat_margin)
