# Prophet Hacks — file-level changelog

Append-only record of what landed when. UTC timestamps. Not a
substitute for git log; this is faster to skim and survives across
branches.

## 2026-05-15 (Friday, all times America/Toronto)

### Evening (~18:00–22:30) — v2 + v3 + v4 + v6 stack landed on disk

- **18:12** Gemini Antigravity baseline merged to `main` via PR #2:
  BenchmarkSession lifecycle, `run_continuous` tick loop, `--once`
  mode, SIGINT/SIGTERM graceful shutdown, agreement-gate ensemble
  (gpt-5.4-mini triage + claude-sonnet-4-6 strong, |p-0.5|≥0.10
  conviction floor), 16 tests pass.
- **18:55** v2 calibrated decomposition pipeline (PR #1) rebased on
  post-Gemini main. Conflicts resolved with orthogonal-layers strategy:
  Gemini lifecycle stays, v2 layers `forecaster.py` 7-stage pipeline,
  v2 introduces `forecasting/{market_blend, source_scoring,
  bidirectional, expert_pool}.py` + `evaluation/*` + `decomposition.py`.
  73 tests pass.
- **19:30** Live HTML monitor (PR #3, draft): auto-refresh dashboard at
  `http://localhost:8765/live.html`, dark theme, 4 SVG panels, schema-
  tolerant against v2/v3 traces. 6 monitor tests pass.
- **20:15** v3 SAE-style "borrowed strength" composer added:
  `forecasting/borrowed_strength.py`, `forecasting/domain_pools.py`
  (Fay-Herriot empirical-Bayes shrinkage per (model, domain)),
  `forecasting/reliability_tracking.py`, `forecasting/uncertainty.py`.
  Workshop-paper framing locked in `docs/BORROWED_STRENGTH.md`. 22 new
  tests in `tests/test_borrowed_strength.py`.
- **21:00** v3 offline pastcasting harness scaffolded: `tools/
  evaluate_offline.py` (8 variants), `tools/check_compliance.py`,
  `scripts/run_offline_eval.py`, `offline/sample_tasks.jsonl` (12
  synthetic events across 6 domains, with longshots and favorites),
  `reports/status.md` front-door template. 6 new tests across
  `test_market_blend / test_source_scoring / test_bidirectional /
  test_evaluation / test_no_leakage / test_expert_pool`.
- **22:00** v6 playbook lock-ins. New files:
  - `forecasting/sae_shrinkage.py` — explicit logit-additive form from
    playbook §16 (domain × horizon × price-bucket random effects, each
    with Fay-Herriot reliability multiplier).
  - `forecasting/prompts.py` — canonical structured-output prompt from
    playbook §19 verbatim. `build_forecast_prompt()` helper.
  - `forecasting/source_gate.py` — retrieval-gate policy. HIGH_SIGNAL
    vs LOW_SIGNAL domains. Returns `RetrievalDecision(should_retrieve,
    reasons, source_cap)`.
  - `connectors/{__init__.py, kalshi.py, polymarket.py}` — optional
    read-only research stubs, gated by `PROPHET_CONNECTORS_LIVE=1`.
  - `tools/ask_project.py` — CLI RAG over three separate KBs (ops /
    research / evidence). BM25-lite over Markdown.
  - `agent_protocol.md` (root) — canonical compact handoff doc for any
    future agent.
  - `tests/test_sae_shrinkage.py` (10 tests), `tests/test_source_gate.py`
    (9 tests).

### Late night (23:30+) — Dataset contract layer landed

- **23:35** Built the schema-normalization layer ahead of the live
  data slate. The forecaster never sees raw event dicts now:
  - `forecasting/schema.py` — canonical `ForecastTask` dataclass with
    `task_id / title / outcomes / yes_label / is_binary / context /
    source / metadata / resolved / raw_event_hash / schema_version_seen`.
    YES-label inference via the standard token list (yes/true/above/over
    + heuristics on comparators). `PredictionOutput` strict-parser with
    [0.01, 0.99] clamping per Prophet Arena scoring rules.
  - `forecasting/normalize.py` — multi-shape input → ForecastTask
    converter. Handles `tasks.jsonl` rows, `events.json` wrappers, server
    POST payloads, pastcast fixtures. Schema-version detection.
    `unwrap_payload()` accepts bare list, `{tasks: [...]}`, `{events:
    [...]}`, `{data: [...]}`, or single-event dict.
  - `forecasting/dataset_loader.py` — schema-tolerant loader. Auto-
    detects: `.jsonl` file, `.json` file, release directory (with
    `release.json + tasks.jsonl`), or sharded JSONL directory. Strict
    and permissive modes; permissive collects errors into
    `LoadResult.errors` without aborting.
  - `tools/inspect_events.py` — dead-simple shape reporter. Prints
    task counts (binary/multi/resolved), field-presence coverage,
    schema-version distribution, category histogram. `--json` flag for
    CI consumption.
  - `tools/validate_events.py` — strict pre-flight gate. Refuses to
    pass if any task fails the schema invariants. Fail-fast before
    we waste a tick.
  - `tools/make_mock_events.py` — fixture generator. Three shapes:
    `binary`, `multi-outcome`, `bad-schema`. Seeds reproducible; bucket
    skew (`--bucket longshot|favorite|any`) for stress-testing the
    Kalshi guards. Outputs JSONL by default, JSON wrapper with `--json`.
  - `tools/evaluate_events.py` — joins tasks ↔ predictions, computes
    Brier / ECE / BSS-vs-market with per-domain and per-price-bucket
    breakdowns. Reports parse failures, missing predictions, and
    unresolved-skipped counts. Markdown out by default.
  - `data/fixtures/toy_binary_events.jsonl` (10 rows, varied buckets
    and domains, unresolved).
  - `data/fixtures/toy_resolved_events.jsonl` (8 rows, all resolved
    YES/NO).
  - `data/fixtures/toy_multi_outcome_events.jsonl` (5 rows: CPI bands,
    MVP race, 4-way election, weather range, FOMC outcome).
  - `data/fixtures/toy_bad_schema_events.jsonl` (6 rows hitting each
    SchemaError code path).
  - `tests/test_event_normalizer.py` (25+ tests: required-field
    enforcement, alternate id/title fields, dedup outcomes, YES-label
    inference, resolved-must-be-in-outcomes, raw_hash stability under
    key reordering, schema-version detection).
  - `tests/test_dataset_loader.py` (15+ tests: each fixture loads
    correctly, strict-vs-permissive modes, release directory layout,
    sharded JSONL directory, empty directory raises, `load_many`
    concatenates).
  - `tests/test_prediction_output_schema.py` (14 tests: boundary
    clamping, missing/wrong-type p_yes, optional p_distribution
    validation, wire-format round-trip).
- **00:10** New top-level doc to be created next session:
  `docs/DATASET_CONTRACT.md` — known / unknown / preparation
  framing for the live data slate.

### Night (22:30+) — Eternis / OpenForecaster lessons folded in

- **22:45** Eternis-Forecaster-8B + OpenForecaster paper analysis
  written to memory. Key insertions: composite reward target (accuracy
  + Brier, not either alone), retrieval cap of 5 chunks per the
  OpenForecaster ablation plateau, hard-example mining criteria,
  confidence-penalty scoring. New files:
  - `forecasting/openforecaster_adapter.py` — optional auxiliary
    expert stub. Only registered as an ensemble member if it beats
    frontier models on our validation set.
  - `tools/mine_hard_cases.py` — discovers high-disagreement / near-
    threshold / longshot-lift / source-conflict examples from a
    JSONL trace. Output → `reports/hard_cases.md`.
  - `tools/evaluate_retrieval_k.py` — sweep retrieval cap k ∈ {0, 1, 2,
    3, 5, 8} on the pastcast set; surface the plateau.
  - `tools/score_confidence_penalty.py` — composite scoring:
    `brier_loss + lambda_overconfident * 1[wrong_dir] * confidence^2 +
    lambda_longshot * 1[lowprob+lift] + lambda_cost * model_call_cost`.
  - `docs/ETERNIS_LESSONS.md` — summary + citations + "do not train 8B
    before core bot stable" anti-pattern.

## How to use this file

Append a new entry under today's date for each significant batch of
changes. Use UTC offset notation if not America/Toronto.

Group entries by morning / afternoon / evening / night so the file
stays scannable at-a-glance. One bullet per coherent change; include
file paths in backticks, key counts (tests, lines), and a one-sentence
rationale when not obvious from the file name.
