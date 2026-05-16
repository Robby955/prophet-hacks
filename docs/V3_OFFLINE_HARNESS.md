# Prophet Hacks v3 — Offline Pastcasting Harness

## The locked thesis

> Do not train a prophet model. Train a prophet operating system.

We do **not** pretrain or fine-tune a forecasting LLM before the live
window. We **pretrain the system around the models**: calibration,
expert weighting, retrieval policy, escalation policy, thresholding,
source credibility, skip/trade rules.

The harness in this branch is the equivalent of "pretraining," but
on the meta-system, against historical resolved markets. See the
strategy memory (`project_prophet_hacks_strategy.md`) for the full
locked v2 / v2.1 / v3 thesis.

## What the harness does

1. Loads a JSONL pastcast dataset (`offline/sample_tasks.jsonl` for
   the synthetic smoke test; real datasets get built by
   `scripts/build_pastcast_dataset.py` from Prophet Arena's
   historical dumps or KalshiBench).
2. Runs every strategy variant against the same tasks, with mocked
   LLM responses when `PROPHET_OFFLINE_MOCK=1` is set (avoids
   burning tokens during a Friday-evening dry run).
3. Scores each variant on Brier, ECE, BSS-vs-market, simulated
   return, Sharpe, coverage by domain, coverage by horizon, cost
   per useful forecast, and skip/trade precision.
4. Emits a markdown table + an HTML comparison report.

## The 8 strategy variants

| Variant | Description |
| --- | --- |
| `market_only` | Predict `p_market` directly. Establishes the baseline we must beat. |
| `gpt55_no_retrieval` | GPT-5.5 structured forecast, no sources. Does the LLM beat the market alone? |
| `gpt55_with_sources` | Same, with ranked retrieved evidence. Does retrieval help? |
| `gpt55_bidirectional` | Ask for `p_yes` and `p_no` separately; combine as `0.5*(p_yes + (1-p_no))`. Improves calibration on 4 of 5 frontier models per Prophet Arena. |
| `gpt55_plus_opus_review` | GPT-5.5 primary + Opus 4.7 review on near-threshold / high-stakes markets. Is Opus worth the cost? |
| `market_blend` | Credibility-weighted logit-pool blend of market + GPT-5.5. With Kalshi longshot guard and favorites no-shrink. |
| `calibrated_ensemble` | All-of-the-above with disagreement-based shrinkage + meaningful-conviction floor. |
| `rl_lite_expert_pool` | Hedge-weighted vote across variants 1–7, weights updated as outcomes resolve (online learning OFF until organizer-rule-confirmed). |

## The 6 pre-live experiments

Each is a controlled comparison the offline harness reports on:

1. **`market_only` vs `gpt55_no_retrieval`** — does the model beat the market prior at all?
2. **`gpt55_no_retrieval` vs `gpt55_with_sources`** — does retrieval help, and in which domains?
3. **direct vs bidirectional** — does bidirectional improve calibration?
4. **`gpt55` vs `gpt55_plus_opus_review`** — is Opus worth the cost, only where?
5. **raw model probability vs `market_blend`** — does our actuarial blend beat raw model output?
6. **fixed threshold vs uncertainty-adjusted threshold** — does adaptive thresholding reduce bad trades?

## No-leakage discipline

The harness REFUSES to score a dataset that has any
`source.published_at > event.forecast_time`. This is enforced by
`evaluation.no_leakage_check.assert_no_leakage` and runs before
every offline eval. See `evaluation/no_leakage_check.py`.

Time-based splits only. **Never** random-split historical markets.

```
train      = older resolved events
validation = newer resolved events
holdout    = newest resolved events before live period
live       = untouched official evaluation
```

## Five things we "fit," not "pretrain"

1. **Calibrator** — simple logistic / isotonic over `logit(p_market), logit(p_gpt), logit(p_opus), source_quality, model_disagreement, time_to_resolution, spread, domain` → outcome. Not a neural net.
2. **Expert weights** — Hedge-style multiplicative updates in `forecasting/expert_pool.py`. Initialized from offline-eval Brier scores via `initialize_from_history`.
3. **Retrieval gate** — heuristic policy on when to spend the LLM call on retrieval vs. trust the market prior.
4. **Escalation policy** — Opus only when the conditions in the strategy memo are met.
5. **Threshold / sizing rules** — `risk.required_edge` extended by `longshot_proximity`; `edge_threshold` tunable by domain/horizon; `MAX_TRADES_PER_TICK` etc. stay locked.

## How to run

```bash
# Mocked-LLM dry run, no token burn
PROPHET_OFFLINE_MOCK=1 python scripts/run_offline_eval.py

# With real APIs (off by default, costs money)
make offline-eval

# Status one-pager
make status

# Pre-submission compliance gate
make compliance
```

The first command should always work, even with no API keys, and
should produce an HTML report under `reports/offline_eval_<ts>.html`.

## What we deliberately do NOT do

- **No model fine-tuning.** High-effort, hard-to-validate, probably
  worse than frontier APIs plus calibration.
- **No deep RL trading policy.** Hedge is the right shape for the
  data regime (sparse outcomes, short evaluation window).
- **No broad web crawl.** Targeted retrieval only, gated.
- **No multi-agent debate.** Theatrical without measurable gain;
  Prophet Arena ablation found reasoning scaffolds don't materially
  improve Brier.
- **No live-trade exploration to train weights.** We freeze the
  pretrained expert weights before submission; online learning is
  enabled only if the organizer-rule check confirms it's allowed.
