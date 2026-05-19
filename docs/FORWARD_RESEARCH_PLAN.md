# Forward Forecasting Research Plan

## Position

Do not change the production endpoint from small shadow samples. Use the live
Prophet Arena stream as the official target, and run shadow experiments as a
separate research loop. Promote only deterministic contract fixes or changes
that clear a predeclared evaluation gate.

## Near-Term Data Loop

1. Keep a rolling queue of events that resolve within 6 hours to 7 days.
2. Record one pre-resolution forecast per event per system.
3. Store the retrieval snippets, evidence URLs, probability vector, rationale,
   model, prompt version, and timestamp.
4. After resolution, score Brier, multiclass Brier, calibration error, and
   market-relative edge when a clean market snapshot exists.
5. Report results by domain, horizon, outcome count, and retrieval quality.

Sports are the fastest calibration source because games resolve cleanly and
frequently. They are not enough. The research set should also include macro,
politics, crypto, weather, entertainment, and corporate events when resolution
rules are crisp.

## Model Matrix

Use production as the control and run candidates only in shadow:

| Arm | Purpose | Promotion Role |
| --- | --- | --- |
| production retrieval | control | current endpoint |
| market-only | lower-bound / abstain baseline | compare whether model adds edge |
| retrieval + Opus | current strong model | main challenger/control |
| retrieval + GPT | cross-vendor independence | ensemble candidate |
| retrieval + Gemini/Grok if available | diversity check | only if schema reliability is clean |
| retrieval + self-consistency | variance reduction | cost/latency study |
| retrieval + calibrator | post-processing only | safest future promotion class |

Do not let the best small-sample arm become production automatically. Require
enough resolved events, stable schema behavior, and no obvious domain-specific
failure.

## Retrieval Audit

For every forecast, save the Brave query and top snippets. Classify each result:

- pre-event evidence
- stale but harmless
- unrelated
- market price or odds
- post-resolution leakage
- low-quality/generated source

The key leakage test is timestamp discipline: if an event is unresolved when
we query, post-resolution leakage should be impossible. If an already-resolved
event is replayed, it must be labeled as contaminated unless we use a
time-frozen corpus.

## Promotion Gate

A production change must satisfy all of:

1. It fixes a schema or scoring contract issue, or it improves shadow Brier by
   a predeclared margin on enough resolved events.
2. It does not degrade binary winner-take-all behavior in tests.
3. It handles top-K, multi-label, and ordered-threshold events without
   normalizing away the intended semantics.
4. It keeps latency inside the evaluator window.
5. It has a one-command revert path.

Until those conditions are met, model comparison remains research only.

## Paper-Grade Angle

The serious research question is not "can one LLM beat the market on a few
games?" The stronger question is:

> When does retrieval-augmented language-model forecasting add calibrated
> information beyond the market, and when does it merely restate the market
> with extra variance?

The publishable shape is a live pre-registered dataset with:

- time-stamped forecasts before resolution
- market baselines captured at prediction time
- retrieval-quality labels
- domain and horizon stratification
- proper scoring rules and market-relative skill
- leakage and contamination audits
- ablations for model, retrieval depth, self-consistency, calibration, and
  abstain-to-market behavior

That gives us a defensible artifact even if the model does not beat the market:
we can identify where the system adds signal, where it should abstain, and how
retrieval quality controls forecast quality.
