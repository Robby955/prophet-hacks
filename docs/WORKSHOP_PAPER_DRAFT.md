# Schema Compliance Dominates LLM Choice in Retrieval-Augmented Probabilistic Forecasting

*A 4-page workshop-paper draft from the Prophet Hacks 2026 submission*

**Author**: Rob Sneiderman  
**Affiliation**: independent; team CanadaHacks / project The Oracles  
**Status**: draft for post-event submission  
**Last updated**: 2026-05-17

---

## Abstract

We report results from a one-weekend probabilistic-forecasting agent
submitted to Prophet Arena. The production pipeline retrieves five
web-evidence snippets via Brave Search, prompts Claude Opus 4.7 with
an explicit market-odds-anchoring instruction, and applies a
Kalshi-paper-informed longshot floor. On a 26-event resolved Prophet
Arena dataset the pipeline scores mean Brier **0.0379**, a 40.7%
relative reduction over a Sonnet 4.6 baseline (paired-bootstrap
95% CI on the delta: [0.0143, 0.0374], 50K resamples, seed pinned).
Two findings dominate the analysis. First, ~85% of the improvement
is attributable to a single bug-fix in the post-LLM longshot floor:
the original formula clamped every binary prediction into [0.25, 0.75]
regardless of model output. Second, swapping the LLM across four
alternative models (Opus 4.6, GPT-5.2, GPT-5.5, Gemini 3.1 Pro)
through an otherwise-identical pipeline yields catastrophic
multi-outcome Brier (0.44 to 0.81 vs 0.018 for Opus 4.7), driven not
by reasoning gaps but by JSON-schema-noncompliance on outcome labels.
We argue that for prompt-strict scoring contracts of this shape, model
selection is dominated by *schema fit*, not by general capability.
Sample size is small (n=26) and binary-skewed (16/26 sports matchups);
results establish directional plausibility, not convergence.

## 1 Introduction

Prophet Arena scores forecasts on the proper score
$\text{Brier}(\mathbf p, \mathbf y) = \sum_{k=1}^{K} (p_k - y_k)^2$,
averaged across events. Each event arrives as
`{title, description, rules, category, close_time, outcomes[]}`;
the agent returns per-outcome probabilities. The benchmark is
adversarial in the sense that confident-and-wrong is punished
quadratically, so calibration matters more than the
probability-of-the-modal-outcome.

This work is a one-weekend submission. The headline metric (mean
Brier on a 26-event resolved sample dataset) and an ablation across
five LLMs through the same pipeline are reported, alongside two
methodological findings the small-sample analysis surfaced.

## 2 Method

### 2.1 Pipeline

The production variant `predict_multi_outcome_retrieval` is five
stages, each logged per call:

1. **Query.** Build a Brave Search query from the event title and
   most informative non-trivial outcome label.
2. **Retrieve.** Hit Brave (`count=5`); degrade gracefully to a
   no-retrieval `predict_multi_outcome` variant if the API call fails
   or the key is unset.
3. **Dedupe + rank.** One result per domain, prioritized by a curated
   domain-priority list (.gov / .edu / exchanges of record / major
   outlets first), cap at five chunks.
4. **Forecast.** Single Anthropic Claude Opus 4.7 call. System prompt
   enforces a 0.50–0.90 calibration scale and an explicit
   *market-odds-anchoring* instruction: if cited implied probabilities
   appear in the evidence, anchor to them and move >5pp only with
   specific contrary signal in the evidence. Output is a strict-JSON
   `{probabilities: {label: float}, rationale: string}`.
5. **Longshot floor.** Each per-outcome probability floored at
   $\min(0.10, \max(0.05, 0.5/n))$ then re-normalized from
   above-floor entries only. The 0.10 cap is the empirical Kalshi
   threshold below which buyers historically lose >60% (the Kalshi
   paper, Whelan 2025).

The choice of 5-stage pipeline reflects a design constraint: every
stage is a step we wanted observable in the per-call trace. The
production endpoint records the Brave query, the deduplicated
evidence URLs, the raw model output, the parser path taken, fuzzy
outcome-label matches, per-stage latency, and any warnings — without
any of those fields appearing in the response sent back to Prophet
Arena.

### 2.2 Schema-resilience layers

Because production LLMs occasionally emit malformed JSON (trailing
commas, smart quotes, slight label drift), the parser is a five-stage
fallback: direct `json.loads`, cleaned `json.loads` (smart quotes +
trailing commas), regex-bounded outer object, regex-bounded outer
object on cleaned text, regex-bounded inner `probabilities` map.
Resolved outcome keys are then matched to canonical labels via a
conservative four-pass match (exact, case-insensitive, whitespace-
stripped, alphanumeric-only), deliberately *not* fuzzy substring —
silently mapping the wrong outcome is worse than falling through to
the uninformed prior.

A separate safety net `_infer_outcomes_when_missing` covers the case
where the live webhook supplies an event without an `outcomes` field:
a binary-question regex returns `["Yes", "No"]`; otherwise a Haiku
4.5 call infers two-to-six plausible labels.

## 3 Experiments

### 3.1 Setup

The 26-event `sample-resolved` dataset from `ai-prophet-datasets`:
Sports (16), Entertainment (4), Elections (3), Politics (3). Binary
events n=14, multi-outcome events n=12 with 3–30 outcomes per event.
Same retrieval and prompt across all rows; only the LLM call swaps.

### 3.2 Multi-LLM ablation

Table 1. Mean Brier across five LLM swaps on the 26-event resolved
set. Same retrieval, same prompt, same longshot floor. Lower is
better.

| Variant | Mean Brier | Binary (n=14) | Multi-outcome (n=12) |
|---|---:|---:|---:|
| **Claude Opus 4.7 (production)** | **0.0379** | 0.0425 | **0.0177** |
| Claude Sonnet 4.6 (previous prod) | 0.0639 | 0.0879 | — |
| Claude Opus 4.6 | 0.2264 | 0.0438 | 0.4396 |
| OpenAI GPT-5.5 | 0.3226 | 0.0376 | 0.6552 |
| OpenAI GPT-5.2 | 0.2584 | 0.0538 | 0.4971 |
| Gemini 3.1 Pro Preview | 0.4149 | 0.0750 | 0.8115 |
| *random 0.5 baseline* | 0.250 | — | — |
| *uniform 1/n prior* | 0.219 | — | — |

The 5× headline gap between production and the next-best alternative
(Opus 4.6) decomposes sharply by outcome count. Binary events show a
factor-of-two range (0.0376–0.0750) — a real but modest reasoning
gap. Multi-outcome events show a *25× to 46× range* (0.018 for
Opus 4.7 vs 0.44–0.81 for the four alternatives).

Inspection of the per-call traces shows the multi-outcome failure
mode is uniformly *JSON-schema noncompliance*: alternative models
emit probabilities for labels not in the supplied outcomes list, or
malformed JSON the five-stage parser ultimately recovers to a
probabilities-map-with-bad-keys. The downstream `_match_outcome_label`
function correctly refuses to map invented keys onto valid outcomes,
so the redistributed mass goes to the uniform prior — which scores
~0.42 on multi-outcome events with a clear favorite.

A control rerun with the same Gemini configuration *after* the
five-stage parser hardening landed produced a near-identical Brier
(0.4149 → 0.4259). The hardening rescues more JSON parses, but the
parsed-but-wrong outputs are themselves wrong.

### 3.3 Decomposing the production improvement

A paired comparison isolates the contribution of the two production
changes:

| Variant | Mean Brier |
|---|---:|
| Sonnet 4.6 + old floor (clamps binary to 0.25) | 0.0639 |
| Sonnet 4.6 + new floor (caps at 0.10) | 0.0418 |
| **Opus 4.7 + new floor (production)** | **0.0379** |

The post-LLM longshot floor accounts for approximately 85% of the
0.0639 → 0.0379 improvement; the Sonnet → Opus 4.7 swap accounts for
the remaining ~15%. The dominant gain is from fixing a silent bug
in post-processing, not from a model upgrade. The previous floor
formula `max(0.05, 0.5/n)` evaluates to 0.25 for binary events,
clamping every binary prediction into [0.25, 0.75] regardless of LLM
output; the corrected formula `min(0.10, max(0.05, 0.5/n))` caps the
floor at the Kalshi-paper empirical threshold.

### 3.4 Statistical significance

Paired-bootstrap of the per-event Brier improvement between Opus 4.7
(production) and Sonnet 4.6 (previous baseline) over the 26 paired
events. 50,000 resamples, seed `20260516` for reproducibility.

| Statistic | Value |
|---|---:|
| Mean improvement | 0.0260 |
| 95% bootstrap CI | [0.0143, 0.0374] |
| n paired events | 26 |

The 95% interval excludes zero; the headline improvement is
significant at α=0.05 on this dataset.

## 4 Discussion

### 4.1 Schema-compliance hypothesis

The 25–46× multi-outcome gap between Opus 4.7 and the four
alternatives is not consistent with a *capability* explanation. All
five models can answer factual questions about the supplied event
outcomes; binary-event Brier is comparable across them (best is
actually GPT-5.5 at 0.0376, marginally better than Opus 4.7's
0.0425). The gap appears entirely on the multi-outcome step, where
the LLM has to emit a JSON object keyed by the *specific outcome
labels* supplied in the prompt.

Opus 4.7 reliably emits a probability for each supplied label.
Opus 4.6, GPT-5.2, GPT-5.5, and Gemini 3.1 Pro Preview reliably emit
JSON for labels they invent (`"Other"`, plain `"Yes"` when the
prompt said `"Yes."`, abbreviated team names) or with smart-quote
contamination or trailing commas. Our parser recovers some of these
but cannot retroactively map invented keys onto the canonical
outcome list without risking silently-wrong outcome attribution.

For prompt-strict scoring contracts of this shape, we conclude that
*schema fit* dominates *general capability* for LLM selection. The
implication for practitioners is that model choice should be
validated empirically against the production parser, not selected
from public leaderboards. Gemini 3.1 Pro Preview, the public Prophet
Arena fixed-context leaderboard top model, placed last in our
pipeline (Brier 0.4149).

### 4.2 Bug-fix dominance

That a post-LLM safety net accounts for 85% of the headline
improvement is itself a finding. The original longshot floor was
intended as a Kalshi-paper-informed cap against vivid-narrative
overconfidence on low-probability outcomes; setting it to `0.5/n`
"half of uniform prior" worked correctly for n ≥ 5 but produced a
silently-wrong 0.25 floor for binary events (n=2). The bug was
caught only by a smoke test on a synthetic Chiefs/Super-Bowl-LXI
event after a separate model-swap unrelated to the floor logic. The
correct cap (the Kalshi empirical 0.10 threshold) is principled and
easy to test; the safety-net's lack of a unit test for the boundary
case was the operational failure.

We note this as a discipline finding rather than a methodological
one. Post-processing safety nets in probabilistic-forecasting
pipelines should have boundary tests at the lowest *n* the pipeline
admits.

### 4.3 Negative result: SAE shrinkage

A separate offline variant `predict_multi_outcome_retrieval_sae`
applies an empirical-Bayes shrinkage estimator with random effects
over (domain × horizon × price) cells, in the spirit of small-area-
estimation borrowed-strength methods. On the same 26-event set the
SAE variant scored mean Brier 0.1157, substantially worse than
production. The variant remains in the repository as research
scaffolding but is not promoted. The architectural assumption —
domain-level pooling stabilizes individual forecasts when sample
sizes are small — runs into the practical problem that the
calibration sample IS the evaluation sample, which either leaks or
fails to learn.

### 4.4 Limitations

The 26-event resolved set is small and binary-skewed (16/26 sports
matchups). The paired-bootstrap CI [0.0143, 0.0374] excludes zero
on this dataset but cannot establish convergence. Multi-outcome
events (n=12) carry less weight per row; multi-outcome events are
also where the schema-compliance gap is most pronounced, so a
balanced-mix evaluation would likely widen the production lead, not
narrow it. We make no claim about live Prophet Arena performance;
that result is forthcoming as the eval window opens.

### 4.5 Related work

Goel et al. (2026, FutureSim, arXiv 2605.15188) introduce a benchmark
that replays real-world events chronologically to evaluate LLM
forecasting agents over a three-month period (Jan–Mar 2026). They
report that the best agent achieves 25% accuracy and that many
agents score worse on Brier skill than making no prediction at all.
This contextualizes our 0.0379 number — even SOTA agents on a larger,
non-leaking dataset struggle — and motivates the schema-compliance
finding here as a productive failure mode to identify before the
eval window opens.

The OpenForecaster work (referenced in the project's decisions log)
reports a Brave-retrieval plateau at five chunks; we adopt that
operating point without further ablation.

The Kalshi paper (Whelan 2025) supplies the empirical >60%-buyer-
loss finding on sub-$0.10 contracts that grounds the 0.10 longshot
floor cap.

## 5 Reproducibility

Source: <https://github.com/Robby955/prophet-hacks>. The production
endpoint serves at <https://agent.forecastingpath.com/predict>; the
auth-protected dashboard at `/dashboard` exposes the full per-call
trace including the artifacts cited above. Headline numbers come
from `data/predictions/multi_outcome_retrieval.json` joined with
`data/resolved.json`; multi-vendor ablation files are in
`data/predictions/ablation_*.json`. The paired-bootstrap CI is
computed by `scripts/bootstrap_brier_ci.py` with the seed pinned.
Full reproduction:

```bash
git clone git@github.com:Robby955/prophet-hacks.git && cd prophet-hacks
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # ANTHROPIC_API_KEY + BRAVE_SEARCH_API_KEY
python scripts/backtest_forecast.py --variants multi_outcome_retrieval
python scripts/bootstrap_brier_ci.py \
  --model data/predictions/multi_outcome_retrieval.json \
  --baseline data/predictions/multi_outcome_retrieval.phase1_sonnet.json \
  --actuals data/actuals.json \
  --seed 20260516 --n-resamples 50000
```

## References

- Goel, S., Chandak, N., Arun, A., Prabhu, A., Staab, S., Hardt, M.,
  Andriushchenko, M., & Geiping, J. (2026). FutureSim: Replaying
  World Events to Evaluate Adaptive Agents. arXiv:2605.15188.
- Prophet Arena Developer Documentation, retrieved 2026-05-16.
  <https://prophetarena.co/developer>
- Whelan, K. (2025). Empirical analysis of buyer behavior on
  small-cap event contracts: the Kalshi paper.
  *(referenced in the project's `docs/DECISIONS.md` 2026-05-16
  locked-decisions entry; primary citation forthcoming.)*
