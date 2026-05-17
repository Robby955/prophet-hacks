# Findings — ForecastingPath / Prophet Hacks 2026

*Author: Rob Sneiderman · Last updated: 2026-05-17*

Empirical and methodological findings from building the
ForecastingPath agent. Separate from `submission/REPORT.md` (the
single-page submission summary) and `docs/RESEARCH_NOTES.md` (the
prior literature-survey of calibration techniques).

Intended reader: someone evaluating the engineering and statistical
work for technical depth — a hiring manager, a reviewer, a future
self picking the project up after the event.

Every number here maps to an on-disk artifact (commit, prediction
file, test). Nothing is speculative.

---

## 1. Setup

**Task.** Given an event `{title, description, rules, category,
close_time, outcomes[]}`, return a probability for each outcome.
Scored by Brier across resolved events; lower is better.

**Production pipeline.** Brave Search (top 5, deduped by domain
priority) → Claude Opus 4.7 with a market-odds-anchoring system prompt
→ Kalshi longshot floor `min(0.10, max(0.05, 0.5/n))`. Authoritative
reference: `forecast_track.py:predict_multi_outcome_retrieval`.

**Test set.** The 26-event `sample-resolved` dataset from
`ai-prophet-datasets`. Categories: Sports (16), Entertainment (4),
Elections (3), Politics (3). Outcome counts: binary (14), n=3 (1),
n=10–30 (11). Binary-skewed; this matters for interpreting results.

**Open-event sets.** 13 economics, 13 entertainment, 16 sports events
from PA datasets pulled via `prophet forecast retrieve`. Used for
cross-model agreement analysis since outcomes are unresolved.

---

## 2. Headline result

| Variant (LLM only swap; pipeline identical) | Mean Brier ↓ | Binary (n=14) | Multi-outcome (n=12) |
|---|---:|---:|---:|
| **Claude Opus 4.7 (production)** | **0.0379** | 0.0425 | **0.0177** |
| Claude Sonnet 4.6 (previous prod) | 0.0639 | 0.0879 | — |
| Claude Opus 4.6 | 0.2264 | 0.0438 | 0.4396 |
| OpenAI GPT-5.5 | 0.3226 | **0.0376** | 0.6552 |
| OpenAI GPT-5.2 | 0.2584 | 0.0538 | 0.4971 |
| Gemini 3.1 Pro Preview | 0.4149 | 0.0750 | 0.8115 |
| Random 0.5 baseline | 0.250 | — | — |
| Uniform 1/n prior | 0.219 | — | — |

**Paired-bootstrap CI on the headline Opus 4.7 vs Sonnet 4.6 delta:**
mean improvement 0.0260, 95% CI [0.0143, 0.0374], 50,000 resamples,
seed `20260516`, n=26 paired events. CI excludes zero; significant at
α=0.05 on this dataset.

**Phase 2 decomposition** (same paired-bootstrap branch):

| Variant | Mean Brier |
|---|---:|
| Sonnet 4.6 + old longshot floor (clamps binary to 0.25) | 0.0639 |
| Sonnet 4.6 + new floor (caps at 0.10) | 0.0418 |
| **Opus 4.7 + new floor (production)** | **0.0379** |

The floor-fix bug accounts for ~85% of the 0.0260 improvement; the
Sonnet→Opus 4.7 swap accounts for ~15%. The dominant gain is from
fixing post-processing, not from the model upgrade.

Source: `data/predictions/{multi_outcome_retrieval,ablation_*}.json`,
joined with `data/resolved.json` ground truth. Brier as defined in
`evaluation/brier.py`.

---

## 3. The dominant driver is JSON schema compliance, not reasoning

The 5-model comparison decomposes by outcome count:

- **Binary events (n=14).** All 6 models within a factor of 2. Opus 4.7
  0.0425, GPT-5.5 *better at 0.0376*, Gemini 3.1 Pro worst at 0.0750.
  The gap is real but not large; reasoning quality matters here.
- **Multi-outcome events (n=12).** Opus 4.7 0.0177 vs Opus 4.6 0.4396 —
  a **25× gap**, with GPT-5.5 at 0.6552 (37× worse) confirming the
  pattern is robust across model families. This is not a reasoning gap.

Inspection of the failure cases (per-call `_trace.warnings`,
`_trace.fuzzy_matches`, and the raw ablation prediction files) shows
the four non-production models (Opus 4.6, GPT-5.2, GPT-5.5, Gemini
3.1 Pro) routinely emit JSON where:

- Keys do not exactly match outcome labels supplied in the prompt
  ("Kansas City" instead of "Kansas City Chiefs"; "Yes." instead of
  "Yes")
- Probability values are assigned to invented labels ("Other" when
  the prompt did not list "Other")
- JSON contains trailing commas, smart quotes, or wraps the
  probabilities map inside outer scaffolding

Each failure mode causes post-processing to fall back to the uniform
1/n prior for that outcome and then redistribute via the longshot
floor — correct behavior given corrupted input, but devastating for
Brier on a multi-outcome event with a clear favorite.

**Implication.** Opus 4.7's advantage on this dataset cannot be
attributed to "the model is smarter." It is most consistent with
"this model follows our specific prompt + JSON schema; the others
have a distribution of failure modes none of which our parser fully
recovers from."

Parser hardening (`_parse_multi_outcome_json` — five-stage fallback)
and fuzzy outcome-label matching (`_match_outcome_label`) keep us
functional if Opus 4.7 has a bad call. They do not rescue an
inherently schema-noncompliant model, as verified by re-running the
Gemini ablation after the hardening landed (Brier 0.4149 → 0.4259;
net neutral — more parses succeed but the underlying probabilities
remain wrong).

---

## 4. A silent production bug worth recording

The original Kalshi longshot guard floor was

```
floor = max(0.05, 0.5 / n_outcomes)
```

For binary events (n=2) this evaluates to **0.25** — clamping every
binary prediction into [0.25, 0.75] regardless of model output. The
bug was not in the LLM call or the prompt; it was in the
post-processing safety net.

Detection. A smoke test on a synthetic event ("Will the Chiefs win
Super Bowl LXI?") returned 0.25 despite the model correctly
identifying the +1500 / ~6% implied market probability in its
rationale. The 0.25 was the floor, not the forecast.

Fix:
```
floor = min(0.10, max(0.05, 0.5 / n_outcomes))
```

The cap at 0.10 is grounded in the Kalshi paper's empirical finding
that sub-$0.10 contracts experience >60% buyer losses; that is the
principled threshold below which a floor is warranted, not "half of
uniform prior." On binary longshots this is ~6× per-event Brier
improvement (a correct 0.06 prediction floored to 0.10 costs
(0.10)² = 0.01 Brier; the same floored to 0.25 costs (0.25)² = 0.0625).

Lesson. Post-processing safety nets need their own unit tests. A
boundary case at n=2 with a confident-correct longshot model output
would have caught this immediately. Added retroactively.

---

## 5. Cross-model agreement as triage signal

Across the 42 unresolved open events, four models (Opus 4.7 production,
Opus 4.6, Sonnet 4.6, GPT-5.2) ran the same pipeline. Per event, we
compute the spread `max(p_yes) - min(p_yes)` across models.

| Dataset | n | Mean spread | Consensus (<0.10) | Mid | Contested (>0.30) |
|---|---:|---:|---:|---:|---:|
| sample-economics | 13 | 0.305 | 8 | 0 | 5 |
| sample-entertainment | 13 | 0.153 | 10 | 1 | 2 |
| sample-sports | 16 | 0.126 | 9 | 4 | 3 |

Economics events have substantially higher cross-model disagreement.
Consistent with the prior that economic questions require integrating
heterogeneous evidence (timing-sensitive announcements, regulatory
context, market-implied probabilities that conflict with point
forecasts). Sports and entertainment events more often have a single
dominant evidence signal.

Operational use. During a live window, the per-call trace includes
evidence URLs and rationale. Categories with historically high spread
can be flagged for review. Not yet wired into automation.

---

## 6. SAE shrinkage: implemented, measured, not promoted

The `forecasting/borrowed_strength.py` and
`forecasting/sae_shrinkage.py` modules implement an empirical-Bayes
shrinkage estimator with random effects over (domain × horizon ×
price) cells. They run in the offline-only variant
`predict_multi_outcome_retrieval_sae`.

Measured Brier on the 26-event sample-resolved set:
**0.1157 vs production 0.0379** — substantially worse. The shrinkage
parameters were not calibrated against a held-out set; live
calibration against single-event resolutions during the eval window
is not supported. The code stays in the repository as research
scaffolding for a post-event paper, not promoted to production.

This is a negative result worth keeping. The architectural assumption
behind SAE here — that domain-level pooling stabilizes individual
forecasts when sample sizes are small — runs into the practical
problem that the alpha calibration sample IS the eval sample, which
either leaks or fails to learn.

---

## 7. Sample-size honesty

n=26 is small. The 95% Brier-difference confidence interval between
Opus 4.7 (0.0379) and Sonnet 4.6 (0.0639) is wide; a paired-bootstrap
CI on the delta would be informative future work.

The dataset is also binary-skewed (16/26 sports matchups). The
multi-outcome Brier numbers (n=12) carry less weight per row.
Multi-outcome events are where alternative LLMs collapse, so this
skew works *against* the magnitude of the headline result for
Opus 4.7; a balanced eval set would likely widen the production
lead, not narrow it.

Live Prophet Arena performance is the only true test. The 0.0379
number establishes directional plausibility under controlled
conditions; it does not establish convergence.

---

## 8. Open questions

1. **Does prompt structure transfer across LLMs?** The market-odds
   anchoring instruction works for Opus 4.7. Whether it rescues
   Opus 4.6 or GPT-5.2 on multi-outcome events, given a stricter
   schema-validation step, is an open empirical question that
   `scripts/ablate_openrouter.py` is set up to answer.

2. **Decomposing the headline improvement.** What fraction is the
   floor-bug fix vs the Sonnet→Opus swap? Re-running the previous
   Sonnet pipeline with the new floor formula would isolate this.
   Estimated cost ~$0.50; not yet run.

3. **Calibration under category shift.** Per Discord clarification,
   the live evaluation will not be category-skewed. Our
   sample-resolved IS sports-skewed; the open-event ablation suggests
   economics is where models genuinely disagree most. A balanced
   eval should expose any category-specific calibration gaps not yet
   measured.

4. **Optimal Brave retrieval count.** OpenForecaster's plateau
   finding (cited in `docs/DECISIONS.md` 2026-05-16 entry 4) puts
   the marginal return at ~5 chunks. Not independently verified on
   our event distribution.

---

## 9. Reproducibility

Every number in this document maps to a file under
`data/predictions/` joined with `data/resolved.json`. The scripts
that produce or reproduce these:

- Headline backtest: `python scripts/backtest_forecast.py --variants multi_outcome_retrieval`
- Multi-vendor ablation: `python scripts/ablate_openrouter.py --model <model_id>`
- Per-event Brier + ECE: `python scripts/analyze_results.py --predictions data/predictions/multi_outcome_retrieval.json --actuals data/actuals.json`
- Live category smoke: `python scripts/smoke_categories.py`
- End-to-end audit: `./scripts/full_check.sh`

Source: <https://github.com/Robby955/prophet-hacks>

---

## Addendum — relevant outside work (2026-05-17)

**FutureSim** (Goel et al., arXiv 2605.15188): a benchmark that
replays real-world events chronologically (Jan-Mar 2026) to evaluate
LLM forecasting agents on Brier and accuracy. Headline: "best agent's
accuracy was 25%, many had worse Brier skill score than making no
prediction at all."

Relevance to this work:
- Reinforces that LLM forecasting is genuinely hard. Our 0.0379 on
  n=26 should not be over-claimed as a generalization; FutureSim's
  larger eval shows even SOTA agents struggle.
- The "worse Brier skill score than no prediction" finding maps to
  what our parser-hardening + outcomes-safety-net work prevents
  on our side: when models confidently emit malformed JSON, falling
  through to uniform prior is empirically a winning move.
- Worth citing in the post-event workshop paper as the broader
  context for our schema-compliance finding.
