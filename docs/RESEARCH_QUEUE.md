# Research queue — post-submission

Things to investigate during the 2-week PA eval window (2026-05-17 to 2026-05-31)
and after. Each item is self-contained: goal, measurement, files to touch,
promotion gate. Pick whichever matches the live distribution we observe.

The non-negotiable rule, unchanged from submission: any production change must
clear the paired-bootstrap CI gate before it ships. |delta| > 0.01 single-binary
Brier AND 95% CI excludes zero on the offline backtest. The 2026-05-17 17:30 CT
entry in `docs/DECISIONS.md` is the most recent precedent — a change that
violated the gate and was reverted before traffic.

## Test-time compute (the 10-minute budget)

PA gives us a 10-minute response window. We use ~3-5 seconds. Reasonable
candidates for spending more, ranked by "research interest × measurability":

### 1. Confidence-gated deep-think
**Goal**: only spend more compute on uncertain forecasts. Fast path for clear
ones, slow path for borderline.

**Mechanism**: run production variant first (~3s). If
`max(probabilities) - second_max < 0.15` (close call), trigger a deep variant:
- k=3 sampling with temperature variation, average in logit space
- Second Brave call with a refined query from the first model's rationale
- Re-prompt with both Brave passes

Budget: deep path costs ~3× production. If only 20% of events hit the slow
path, marginal spend is ~60% of current. Total well under 30s/event.

**Files**: new variant `predict_confidence_gated_deep_think` in
`forecast_track.py`; wire as opt-in via env `PROPHET_AGENT_VARIANT`.

**Measurement**: backtest against 26-event resolved set; compute paired-bootstrap
CI against current production. Promotion gate as usual.

**Open hypothesis**: the regression bucket on n=26 is small-delta uncertain
events. Spending more on those specifically should shift them toward correct
without disturbing confident-and-correct.

### 2. Iterative re-retrieval (tool-use style)
**Goal**: let the model ask for more evidence when the first Brave pass
doesn't cover the question.

**Mechanism**: after first forecast, ask the model "do you need additional
search? if yes, write one specific query." If yes, second Brave call; final
forecast incorporates both.

**Files**: new variant `predict_iterative_retrieval` in `forecast_track.py`.

**Budget**: ~2× current. Decide if the answer-quality lift exceeds cost.

**Measurement**: same backtest + bootstrap CI. Specifically log Brave query
count per event to study which event types trigger the extra call.

### 3. Multi-model fact-checking (not voting)
**Goal**: Opus generates forecast with rationale → smaller model extracts
specific factual claims → checks each against a fresh Brave call → returns
corrected facts to Opus for a final pass.

**Files**: orchestration in `forecast_track.py`; reuse existing Anthropic
client + Brave wrapper.

**Risk**: this is a different shape than self-critique (which regressed in our
earlier ablations). The hypothesis is that *fact-checking specific claims*
is different from *re-rating the whole forecast*.

### 4. Counterfactual generation
**Goal**: ask the model to write the strongest case for each outcome before
assigning probabilities. The hypothesis is that explicit advocacy raises the
floor on outcomes we're tempted to under-weight.

**Files**: prompt change to existing variant, OR new variant.

**Caveat**: this is conceptually adjacent to self-critique (which regressed).
Measurement before deploy is non-negotiable.

## Calibration drift correction (during eval)

### 5. Live empirical-bayes shrinkage
**Goal**: as PA calls land and resolve, log empirical calibration per
probability bucket (0.10–0.20, 0.20–0.30, …, 0.80–0.90). Apply a learned
correction post-LLM.

**Existing scaffolding**: `forecasting/sae_shrinkage.py` and
`forecasting/borrowed_strength.py` already contain the borrowed-strength
estimator. Not wired into production today.

**Wire**: after `_predict_multi_outcome_retrieval_impl` returns, before
`apply_longshot_guard`, pass through a `bayes_shrinkage(probs, history)` step.
History reads from `data/predictions/*.json`.

**Measurement**: this is online. Track per-event Brier with/without shrinkage,
update the prior at each tick. Document on a new
`/static/calibration_drift.html` page.

**Risk**: shrinkage acts on small samples in the early window. Set a minimum
sample size of N=20 events resolved before any shrinkage kicks in. Until then,
the function is identity.

## Ablation experiments (offline, no risk to live)

These don't touch /predict. They run against `data/resolved.json` and feed the
post-event retrospective.

### 6. Sensitivity to Brave count
We documented in DECISIONS that 3/5/8 sweep failed the promotion gate. Re-run
with the live PA event format (whatever distribution we see in the first
batch) to confirm the n=26 finding generalizes.

### 7. Anchor-block removal (full PR #12)
The bundled PR #12 measurement showed multi-only Brier 0.4551 → 0.3622 with
all three changes including anchor removal. Our partial 4+5 measurement
showed single-binary regression. Worth a clean three-way ablation:
- baseline (current)
- 4+5 only (what regressed)
- 4+5+anchor-removal (PR #12 full bundle)

Bootstrap CI on each pairwise comparison. Document which combination, if any,
clears the promotion gate.

### 8. Cross-model on Subset-1200
We did Opus 4.7 on the 1200-event scale set. Cross-model rankings on n=26
were Opus 4.7 > Opus 4.6 > GPT-5.2 > Sonnet 4.6 > GPT-5.5 > Gemini. Worth
verifying ranking holds at scale. Costs ~$300-500 per model; budget
permitting, run Opus 4.6 first (closest to production).

## Upstream PR opportunities (`ai-prophet/ai-prophet`)

PA's repo has 0 open issues as of submission. After the eval window we'll
have a list of real friction points to file. Provisional list, fill in as we
hit them:

- [ ] **Event schema clarity**: PA's docs describe an "Event Input Shape"; our
  schema accepts `extra="allow"` to tolerate drift. If we hit specific
  unexpected fields, file an issue.
- [ ] **`/health` vs `/healthz` documentation**: Leon's Discord said `/health`;
  we support both. If their docs only say `/health`, file a clarification PR.
- [ ] **OpenAI-compatible vs Event-shape endpoint**: Devpost said
  "OpenAI-compatible," Discord said Event-shape. We built both
  (`/v1/chat/completions` shim exists). File an issue documenting which is
  the actual contract.
- [ ] **Scoring rule transparency**: their CLI evaluator does single-binary
  Brier on `outcomes[0]`; their docs describe proper multi-class Brier; live
  is BSS against snapshotted market prices. Three rules; the discrepancy
  could be made explicit in their docs.
- [ ] **SDK trading-track docs**: out of scope for forecasting submission but
  if we ever look at trading, the slug/n_ticks/starting_cash workflow is
  documented entirely in Discord.

## Site / dashboard expansions

Lower priority but cheap and reads well:

- **Live PA call feed**: `/dashboard` already has SSE for predictions. After
  first PA call lands, add a "PA call history" panel with per-event Brier
  if the actual is in.
- **Per-deploy Brier drift**: tag each commit's predictions, render a line
  chart of cumulative live Brier over time, broken out by commit SHA. Useful
  for the retrospective.
- **Variant gallery extension**: today shows 6 model variants on the resolved
  backtest. Add the deep-think variant after #1 measures.

## Methodology hygiene

- **Pre-merge backtest CI**: every prompt or variant change should have its
  bootstrap CI run before merge to main. The 2026-05-17 17:30 entry is the
  precedent — measure first, then merge.
- **Tag mid-window deploys**: every deploy during PA eval gets a DECISIONS
  entry with SHA + UTC time so the retrospective can attribute Brier deltas
  to specific commits.
- **Variance run cadence**: re-run the 5-rerun variance estimate on each new
  variant to know its noise floor before treating any delta as signal.

## Working with this file

Pick an item. State the goal explicitly. Run the measurement script. Get the
bootstrap CI. If it clears the gate, propose a PR. If it doesn't, log the
attempt in DECISIONS.md as a negative result and pick the next item.

`scripts/backtest_forecast.py` + `scripts/bootstrap_brier_ci.py` are the
workhorse pair. `scripts/ablate_openrouter.py` is the multi-vendor harness.
`scripts/check_retrieval_leakage.py` checks for hindsight in any new dataset.

No deploy ships without the gate cleared.
