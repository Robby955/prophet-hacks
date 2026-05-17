# Post-LLM Safety Nets and Schema Discipline in Retrieval-Augmented Probabilistic Forecasting

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
Arena dataset the pipeline scores mean Brier **0.0378** under
single-binary scoring (the metric Prophet Arena's CLI evaluator
implements), a 40.8% relative reduction over a Sonnet 4.6 baseline
(paired-bootstrap 95% CI on the delta: [0.0143, 0.0374], 50K
resamples, seed pinned). Three findings dominate the analysis.
First, ~85% of the headline improvement is attributable to a single
bug-fix in the post-LLM longshot floor: the original formula
clamped every binary prediction into [0.25, 0.75] regardless of
model output. Second, the *scoring rule itself* materially changes
the conclusions — under proper multi-class Brier (sum across all
outcomes per event, as Prophet Arena's published formula suggests),
Opus 4.6 marginally outperforms Opus 4.7 (0.2500 vs 0.2558). Third,
swapping the LLM across four alternative models (Opus 4.6, GPT-5.2,
GPT-5.5, Gemini 3.1 Pro Preview) through an otherwise-identical
pipeline reveals a spectrum: Opus 4.6 and GPT-5.2 are competitive,
while GPT-5.5 and Gemini exhibit multi-outcome Brier 18–46× worse,
driven by JSON-schema-noncompliance on outcome labels rather than
reasoning gaps. Sample size is small (n=26) and binary-skewed
(16/26 sports matchups); results establish directional plausibility,
not convergence.

## 1 Introduction

Prophet Arena's actual scoring rule, confirmed by PA organizers in
Discord on 2026-05-16 ("Total score = (team avg Brier − market avg
Brier) × completion rate"), is a *Brier skill score against a
live-market baseline*. The market baseline is the Brier of
snapshotted Kalshi or Polymarket prices at prediction time, on
events that close 2 days to 2 weeks out. Beating the market by a
small margin in Brier produces a small positive total score;
producing higher Brier than the market produces a negative score.
Completion rate (fraction of webhook events the agent successfully
returned a forecast on) is a multiplicative term — a 500 error on
10% of events scales the headline by 0.9.

This rule differs from both of the offline metrics one would compute
from PA's published submission docs. The published formula
$\text{Brier}(\mathbf p, \mathbf y) = \sum_{k=1}^{K} (p_k - y_k)^2$
suggests proper multi-class Brier; PA's CLI evaluator
(`prophet forecast evaluate`) implements *single-binary* Brier on
`(p_yes − 1{outcomes[0] won})²`. We report both throughout because
they ranked our model lineup differently on our 26-event resolved
backtest, and the actual live metric was not known to us until after
all ablations were run.

Each event arrives as
`{title, description, rules, category, close_time, outcomes[]}`;
the agent returns per-outcome probabilities. The benchmark is
adversarial in the sense that confident-and-wrong is punished
quadratically, so calibration matters more than the
probability-of-the-modal-outcome.

This work is a one-weekend submission. The headline metric (mean
Brier on a 26-event resolved sample dataset, scored both ways) and
an ablation across five LLMs through the same pipeline are reported,
alongside three methodological findings the small-sample analysis
surfaced.

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

We report Brier under **both** scoring conventions throughout
because the choice materially changes which model looks best:

- **Single-binary**: `(p_yes − 1{outcomes[0] won})²`, the metric
  PA's `prophet forecast evaluate` CLI implements.
- **Multi-class**: `sum_k (p_k − 1{k == winner})²`, the metric
  PA's published formula suggests and the standard proper score.

### 3.2 Multi-LLM ablation

Table 1. Mean Brier across five LLM swaps on the 26-event resolved
set. Same retrieval, same prompt, same longshot floor. Lower is
better.

| Variant | Single-binary ↓ | Multi-class ↓ | Multi-only (n=12) ↓ |
|---|---:|---:|---:|
| **Claude Opus 4.7 (production)** | **0.0378** | 0.2558 | 0.4551 |
| Claude Opus 4.6 (PA leaderboard top agent) | 0.0391 | **0.2500** | 0.4396 |
| OpenAI GPT-5.2 | 0.0438 | 0.2874 | 0.4971 |
| Claude Sonnet 4.6 (previous prod) | 0.0639 | — † | — |
| OpenAI GPT-5.5 | 0.0920 | 0.3429 | 0.6552 |
| Gemini 3.1 Pro Preview | 0.0983 | 0.4773 | 0.8115 |
| *random 0.5 baseline* | 0.250 | — | — |
| *uniform 1/n prior* | 0.219 | — | — |

† Sonnet's prediction file pre-dates the bug fix that adds the full
per-outcome `probabilities` array (§3.4); its multi-class number
would reflect the 1/n uniform fallback, not real model behavior.
Single-binary is unaffected.

**Honest framing.** Under PA's CLI metric, Opus 4.7 wins by **3.4%**
over Opus 4.6 (0.0378 vs 0.0391) — a real but modest gap. Under
proper multi-class scoring, Opus 4.6 is marginally *better* (0.2500
vs 0.2558). Model selection is metric-dependent on n=26. We hold
Opus 4.7 in production because PA's CLI scoring is the only metric
we can verify on a resolved dataset, and because the
Sonnet→Opus 4.7 transition is the production-tested path. The
choice should be revisited if live PA scoring turns out to be
multi-class.

The two-tier failure mode is clearer on multi-outcome events.
Opus 4.7, Opus 4.6, and GPT-5.2 cluster at multi-only Brier
0.44–0.50. GPT-5.5 jumps to 0.66 and Gemini 3.1 Pro Preview to
0.81 — 25× to 46× worse than production on the same rows. Inspection
of the per-call traces shows the bottom-tier failure mode is
uniformly *JSON-schema noncompliance*: GPT-5.5 and Gemini emit
probabilities for labels not in the supplied outcomes list, or
malformed JSON the five-stage parser ultimately recovers to a
probabilities-map-with-bad-keys. The downstream `_match_outcome_label`
function correctly refuses to map invented keys onto valid outcomes,
so redistributed mass goes to the uniform prior — which scores
~0.42 on multi-outcome events with a clear favorite.

A control rerun with the same Gemini configuration *after* the
five-stage parser hardening landed produced a near-identical Brier
(0.4149 → 0.4259). The hardening rescues more JSON parses, but the
parsed-but-wrong outputs are themselves wrong.

### 3.3 Decomposing the production improvement

A paired comparison isolates the contribution of the two production
changes (single-binary scoring):

| Variant | Mean Brier |
|---|---:|
| Sonnet 4.6 + old floor (clamps binary to 0.25) | 0.0639 |
| Sonnet 4.6 + new floor (caps at 0.10) | 0.0418 |
| **Opus 4.7 + new floor (production)** | **0.0378** |

The post-LLM longshot floor accounts for approximately 85% of the
0.0639 → 0.0378 improvement; the Sonnet → Opus 4.7 swap accounts for
the remaining ~15%. The dominant gain is from fixing a silent bug
in post-processing, not from a model upgrade. The previous floor
formula `max(0.05, 0.5/n)` evaluates to 0.25 for binary events,
clamping every binary prediction into [0.25, 0.75] regardless of LLM
output; the corrected formula `min(0.10, max(0.05, 0.5/n))` caps the
floor at the Kalshi-paper empirical threshold.

### 3.4 A methodology bug we caught

A prompt-variant ablation (V0 production vs V1 no-anchor) returned
identical Brier (0.0378), which seemed suspicious for a non-trivial
prompt change. Tracing the comparison revealed that
`backtest_forecast.py` had been dropping the per-outcome
`probabilities` array from each saved prediction, keeping only
`p_yes`. The summary-report generator then defaulted to a uniform
1/n distribution for proper multi-class scoring, producing
artificially-low multi-class Brier numbers for the production
variant while alternative-model variants (run through a different
ablation script that preserved the array) were scored on their
real distributions. The cross-model comparison was thus
apples-to-oranges by metric, not by model. The fix
(`scripts/backtest_forecast.py:60`) stores `probabilities` and
`evidence_urls` on every prediction; the summary generator
(`scripts/build_summary_report.py`) now computes both single-binary
*and* multi-class Brier consistently across all variants. All
audience-facing numbers in this paper, the README, and the
submission report are the post-fix re-run.

### 3.5 Statistical significance

Paired-bootstrap of the per-event Brier improvement between Opus 4.7
(production) and Sonnet 4.6 (previous baseline) over the 26 paired
events. 50,000 resamples, seed `20260516` for reproducibility.
Single-binary scoring.

| Statistic | Value |
|---|---:|
| Mean improvement | 0.0260 |
| 95% bootstrap CI | [0.0143, 0.0374] |
| n paired events | 26 |

The 95% interval excludes zero; the headline improvement is
significant at α=0.05 on this dataset under single-binary scoring.
We did not compute a multi-class CI because the Sonnet file
pre-dates the per-outcome-probabilities fix; re-running Sonnet
through the corrected pipeline is post-event work.

## 4 Discussion

### 4.1 Three scoring rules, three different rankings

The single-largest finding from this weekend is that *the scoring
rule itself* changes which model looks best. We encountered three
distinct rules during the bench:

- **PA CLI** (`prophet forecast evaluate`): single-binary on
  `(p_yes − 1{outcomes[0] won})²`.
- **PA published docs**: proper multi-class Brier summed across all
  per-outcome labels.
- **PA actual live scoring** (confirmed by organizers in Discord
  partway through the bench): `(team avg Brier − market avg Brier)
  × completion rate`. A Brier skill score with a market baseline.

On the 26-event resolved set we can verify the first two but not
the third (no snapshotted market prices in the dataset). The first
two metrics rank the top-three models differently:

- Single-binary: Opus 4.7 > Opus 4.6 > GPT-5.2 (by 3.4% and 12% margins)
- Multi-class: Opus 4.6 > Opus 4.7 > GPT-5.2 (by 2.3% and 13% margins)

GPT-5.5 and Gemini are bottom-ranked under both metrics, so the
schema-compliance finding (§4.2) holds. But the choice between
Opus 4.7 and Opus 4.6 in production is metric-dependent and on
n=26 cannot be settled. We chose Opus 4.7 because PA's CLI metric
is the only one we can verify against resolved data, and because
we tested the Sonnet → Opus 4.7 transition end-to-end.

Once we learned the actual live rule, the strategic implications
flipped on one of our ablation findings. A prompt-variant ablation
removing the market-odds-anchoring block from the system prompt
showed a +0.0192 multi-only Brier improvement against actual
outcomes (n=12 multi-outcome events). Under the live rule this is
not a clear win: anchoring to cited market prices is *protective*
against negative score when the market has signal we lack, and the
+0.0192 only translates to positive total score if it survives
re-measurement against market Brier rather than outcome Brier. We
did not delete the anchoring block. A clean re-run with snapshotted
market prices is post-event work.

Practitioners should pin the scoring rule to the *exact* evaluator
they will be graded by, and verify before treating ablation deltas
on offline metrics as license to ship.

### 4.2 Schema-compliance hypothesis (revised)

The 18×–46× multi-outcome gap between the top-three cluster and the
bottom two is not consistent with a *capability* explanation. All
five models answer factual questions about the supplied event
outcomes; binary-event Brier is comparable across them (best is
actually GPT-5.5 at 0.0376 on the binary subset, marginally better
than Opus 4.7's 0.0425). The gap appears on the multi-outcome step,
where the LLM has to emit a JSON object keyed by the *specific
outcome labels* supplied in the prompt.

The pattern, qualified by the corrected numbers:

- **Opus 4.7, Opus 4.6, GPT-5.2**: reliably emit per-label
  probabilities. Multi-only Brier 0.44–0.50.
- **GPT-5.5, Gemini 3.1 Pro Preview**: reliably emit JSON for labels
  they invent (`"Other"`, plain `"Yes"` when the prompt said `"Yes."`,
  abbreviated team names) or with smart-quote contamination or
  trailing commas. Our parser recovers some of these but cannot
  retroactively map invented keys onto the canonical outcome list
  without risking silently-wrong outcome attribution. Multi-only
  Brier 0.66–0.81.

The strongest claim we can defend with n=26 is that *schema
discipline is a real selection criterion*, GPT-5.5 and Gemini fail
it on this pipeline, and the three top models pass it. We do not
claim schema-compliance "dominates" model selection in general —
the top three are clustered tightly enough that the within-cluster
choice depends on other factors. The implication for practitioners
is that model choice should be validated empirically against the
production parser, not selected from public leaderboards. Gemini
3.1 Pro Preview, the PA fixed-context leaderboard top model, placed
last in our pipeline (Brier 0.0983 single-binary, 0.4773 multi-class).

### 4.3 Bug-fix dominance

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

### 4.4 Negative result: SAE shrinkage

A separate offline variant `predict_multi_outcome_retrieval_sae`
applies an empirical-Bayes shrinkage estimator with random effects
over (domain × horizon × price) cells, in the spirit of small-area-
estimation borrowed-strength methods. On the same 26-event set the
SAE variant scored mean Brier 0.1157 (single-binary), substantially
worse than production. The variant remains in the repository as
research scaffolding but is not promoted. The architectural
assumption — domain-level pooling stabilizes individual forecasts
when sample sizes are small — runs into the practical problem that
the calibration sample IS the evaluation sample, which either
leaks or fails to learn.

### 4.5 Limitations

The 26-event resolved set is small and binary-skewed (16/26 sports
matchups). The paired-bootstrap CI [0.0143, 0.0374] excludes zero
on this dataset under single-binary scoring but cannot establish
convergence. Multi-outcome events (n=12) carry less weight per row;
multi-outcome events are also where the schema-compliance gap is
most pronounced, so a balanced-mix evaluation would likely widen
the production lead under single-binary and *narrow or invert* it
under multi-class. We make no claim about live Prophet Arena
performance; that result is forthcoming as the eval window opens.

### 4.6 Related work

Goel et al. (2026, FutureSim, arXiv 2605.15188) introduce a benchmark
that replays real-world events chronologically to evaluate LLM
forecasting agents over a three-month period (Jan–Mar 2026). They
report that the best agent achieves 25% accuracy and that many
agents score worse on Brier skill than making no prediction at all.
This contextualizes our numbers — even SOTA agents on a larger,
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
Both single-binary and multi-class Brier are computed by
`scripts/build_summary_report.py` on the same prediction files.
Full reproduction:

```bash
git clone git@github.com:Robby955/prophet-hacks.git && cd prophet-hacks
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # ANTHROPIC_API_KEY + BRAVE_SEARCH_API_KEY
python scripts/backtest_forecast.py --variants multi_outcome_retrieval
python scripts/build_summary_report.py
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
