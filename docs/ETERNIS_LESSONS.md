# Eternis / OpenForecaster — what to steal, what to avoid

Source notes from Rob's analysis 2026-05-15 night. Eternis Labs
announced Eternis-Forecaster-8B, an 8B model post-trained for
open-ended world-event forecasting; the underlying paper is the
OpenForecaster paper accompanied by the OpenForesight benchmark.

Treat reported performance numbers as **interesting but not gospel
until inspected**. The methodological lessons are the durable value.

## What we steal (running list)

### 1. Context quality is the primary driver

The Eternis blog frames "context building" as the single biggest
contributor to forecast accuracy. They use structured search +
stronger reasoning models to curate decision-relevant passages
BEFORE the forecaster sees the task.

**Our implementation**: `forecasting/source_gate.py` decides WHEN to
retrieve and caps the source count. Cheap reasoning models triage
which passages are decision-relevant before the strong model sees
them. Source-quality ranking happens in `forecasting/source_scoring.py`.

### 2. Retrieval plateau at ~5 chunks

OpenForecaster paper finding: retrieval improvements plateau after
about 5 retrieved chunks. They split articles into 512-token chunks
+ embedded + retrieved top-k.

**Our implementation**: `tools/evaluate_retrieval_k.py` sweeps k ∈ {0,
1, 2, 3, 5, 8} on our pastcast set. Verify our own plateau before
committing to a default. Today's `source_gate` caps at 3 by default
because pre-OpenForecaster heuristics suggested 2–3 was the sweet
spot for binary-market forecasting; we'll update based on the sweep.

### 3. Composite reward target

OpenForecaster GRPO ablation:
- Accuracy-only reward → hurts calibration (overconfidence)
- Brier-only reward → hurts exploration (regresses to 0.5)
- Accuracy + Brier composite → outperforms either alone

**Our implementation**: `tools/score_confidence_penalty.py`:

```
score = brier_loss
      + lambda_overconfident * 1[wrong_direction] * confidence^2
      + lambda_longshot * 1[p_market<0.10 AND model_lift_large]
      + lambda_cost * model_call_cost_usd
      + lambda_fragile_source * fragile_source_count
```

Used for offline variant ranking, hyperparam search targets, and as
a runtime sanity check on the trade gate (a forecast whose composite
score is bad should not trade even if naive edge clears threshold).

### 4. Hard-example mining

Eternis "hard examples" companion post argues GRPO gains are largest
when training on hard cases because they maintain nonzero reward
variance longer (= more learnable signal).

**Our implementation** (we don't fine-tune, same principle applies to
prompt / calibrator / stacker improvements): `tools/mine_hard_cases.py`
flags rows in our JSONL trace that satisfy any of:

- market vs model disagreement > 0.15
- GPT vs Opus disagreement > 0.12
- retrieval source_disagreement > 0.30
- longshot lift (p_market < 0.10 + model_lift > 0.05)
- near-threshold + uncertainty_aggregate > 0.30
- high-confidence miss (|p_final − 0.5| > 0.30 AND wrong direction)

Output `reports/hard_cases.md` is the manual-inspection feedstock.

### 5. Optional auxiliary expert, never replacement

If Eternis-Forecaster-8B or OpenForecaster-8B is available and
runnable, we register it as ONE ensemble member alongside GPT-5.5
and Claude Opus 4.7, gated by `forecasting/openforecaster_adapter.should_register_in_expert_pool`:

- Requires ≥ 50 holdout outcomes from this model
- Requires Brier beating the best frontier expert by ≥ 0.005 absolute

Only after both conditions does it earn live weight via the Hedge
expert pool's empirical-Bayes update.

## What we do NOT do

- **Do NOT** start training or fine-tuning an 8B model before the
  core bot is stable. That's the trap. We add the hooks; we don't
  light the fire.
- **Do NOT** use OpenForecaster's open-ended question scoring 1:1.
  Open-ended forecasting and prediction-market binary trading are
  related but not identical scoring shapes. Brier + monetary edge are
  what we get scored on, not generative QA.
- **Do NOT** replace frontier models with the 8B specialized
  forecaster without empirical validation on our own pastcast set.
- **Do NOT** copy GRPO machinery into the contest agent. That's
  post-training infrastructure; live inference uses pretrained
  weights.

## References (verify URLs before citing)

- Eternis Labs blog: Eternis-Forecaster-8B announcement (2025).
- OpenForecaster paper (arXiv): ~50K open-ended questions from news,
  static offline news snapshots to avoid leakage, retrieval + GRPO
  fine-tuning of Qwen3-thinking, composite accuracy + Brier reward.
- OpenForesight benchmark dataset: open-ended world-event forecasting.
- Companion post: hard-example mining for GRPO.

## How to apply in the next iteration

When the offline pastcast harness has ≥ 200 resolved outcomes:

1. Run `python tools/mine_hard_cases.py --trace traces/*.jsonl`
2. Inspect `reports/hard_cases.md` by hand
3. For each top failure mode:
   - Adjust prompt language → re-run mock eval → measure delta
   - Add a specific check or skip rule → unit test it
   - If a stratum (domain × horizon × price-bucket) systematically
     misses, fit its `alpha` in `forecasting/sae_shrinkage.py` from
     the residuals on the holdout split
4. Re-run `python tools/score_confidence_penalty.py --trace ...` and
   compare composite scores variant-by-variant. Lowest total score
   wins, not lowest Brier alone.
5. Lock the resulting calibrator snapshot before live submission.

## Anti-pattern explicitly

> "I'll just fine-tune Qwen-8B on my pastcast set tonight."

No. The contest agent must be reproducible from frontier APIs alone.
Local model inference adds: dependency on local GPU, risk of license
violations, fragile reproduction story for the submission, and a
calibration loop that hasn't shipped yet. Frontier APIs + good
calibration + hard-case mining are higher leverage in a 30-hour
window.
