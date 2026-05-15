# Decisions log

Chronological record of design and policy decisions. Append-only.
One heading per decision. Two to four bullets each: the decision, the
rationale, who or what made it, and any related commit SHA.

---

## 2026-05-15 · pinned package versions

- `ai-prophet-core==0.1.4` and `ai-prophet==0.1.4` pinned in `requirements.txt`.
- Rationale: only verified-working SDK version against the 0.1.4 wire models in `client_models.py`. We are not chasing a moving target during a 24-hour event.
- Decided by: Rob.
- Commit: `15c0ae0` (initial skeleton).

## 2026-05-15 · edge threshold 0.08

- `EDGE_THRESHOLD = 0.08` in `risk.py`. Trade fires when `p_yes - yes_ask >= 0.08` (or the NO analogue).
- Rationale: trades only when the forecast cushion exceeds plausible mispricing of the spread itself. Below 0.08 the expected value bleeds to the spread.
- Decided by: Rob (skeleton brief).
- Commit: `15c0ae0`.

## 2026-05-15 · max 3 trades per tick, max 5 markets analyzed

- `MAX_TRADES_PER_TICK = 3`, `MAX_MARKETS_ANALYZED_PER_TICK = 5`.
- Rationale: bounds model spend per tick and caps within-tick concentration risk. Three is enough to act on multiple decent opportunities without crowding into correlated bets.
- Decided by: Rob (skeleton brief).
- Commit: `15c0ae0`.

## 2026-05-15 · bucketed probabilities

- Forecasts snap to `BUCKETS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]`. Default forecast returns 0.50 (no view).
- Rationale: prevents false precision. A model that emits 0.732 is rarely better-calibrated than the same model emitting 0.70. The calibration table in `SUBMISSION_NOTES.md` ties each bucket to a verbal meaning.
- Decided by: Rob (skeleton brief).
- Commit: `15c0ae0`.

## 2026-05-15 · no retrieval in the skeleton

- `policy.retrieval_enabled = false` in `config.yaml`. `forecaster.forecast_model_with_retrieval` raises `NotImplementedError`.
- Rationale: retrieval adds a slow, error-prone dependency that hides forecast-quality regressions. We can prove the agent without it, and bolt it on as a controlled experiment.
- Decided by: Rob (skeleton brief).
- Commit: `15c0ae0`.

## 2026-05-15 · triage routed to OpenAI gpt-5.4-mini (NOT Haiku)

- `models.triage = openai/gpt-5.4-mini`.
- Rationale: Rob's preference for the OpenAI mini tier on cheap-fast tasks; better latency and cost profile for triage on this workload.
- Note: the brief specified `gpt-5.5-mini`. The canonical OpenAI Python SDK literal list (`openai/openai-python` repo, `src/openai/types/shared/chat_model.py`) shows no `gpt-5.5` family exists. Most recent family is `gpt-5.4`. Using verified `gpt-5.4-mini`.
- Decided by: Rob (pref) plus model-id verification against the SDK.
- Commit: this branch.

## 2026-05-15 · forecast stays on Anthropic Opus 4.7

- `models.forecast = anthropic/claude-opus-4-7`. Fallback chain on 5xx or rate-limit: Sonnet 4.6, then Haiku 4.5.
- Rationale: Opus 4.7 is the strongest available reasoner. Anthropic batch API is on (50% discount) for non-live forecast runs; OpenAI batch is off (async 24h is not useful for live ticks).
- Decided by: Rob.
- Commit: this branch.

## 2026-05-15 · org-level model access verified

- Anthropic `models.list()`: `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` all present. Config left as-is.
- OpenAI `models.list()`: org has access to the `gpt-5.5` family (`gpt-5.5`, `gpt-5.5-2026-04-23`, `gpt-5.5-pro`, `gpt-5.5-pro-2026-04-23`) AND the full `gpt-5.4` family. Note that the openai-python SDK literal list previously consulted lagged behind production.
- However, there is still NO `gpt-5.5-mini`. The mini tier tops out at `gpt-5.4-mini`. Triage stays on `gpt-5.4-mini`.
- Open option: if Rob wants a non-Anthropic forecast variant, `gpt-5.5-pro` is now available as the OpenAI strongest model option.
- Decided by: org-level `models.list()` call against both providers.
- Commit: this branch.

## 2026-05-15 · multi-model agreement gate (stretch goal)

- Considered: only fire a trade when triage and forecast agree on direction (both YES-edge or both NO-edge).
- Status: deferred. Not in the skeleton. Promote to a separate forecaster variant once the single-model path is proven on a real tick.
- Decided by: Rob.
- Commit: not yet wired.
## 2026-05-15 · BenchmarkSession lifecycle + continuous loop + --once mode

- Wrapped the tick agent in `ai_prophet_core.arena.BenchmarkSession`. Added `run_continuous` main loop that claims ticks until experiment completes; SIGINT/SIGTERM trigger graceful shutdown after the current tick. Added `--once` flag for one-shot CI/smoke runs. Tick failures finalize with `status="FAILED"` and `error_code="TICK_ERROR"` so the experiment advances rather than wedging.
- Decided by: Rob.
- Commit: `db25183` (feat/2026-05-15-prep-config-and-coordination).

## 2026-05-15 · agreement-gate ensemble (Gemini baseline)

- Two-model ensemble: `gpt-5.4-mini` triage + `claude-sonnet-4-6` strong. Trades fire only when both models agree on direction AND both have `|p - 0.5| >= 0.10` (meaningful conviction).
- Rationale: cheap-then-expensive routing keeps cost down; the agreement gate filters out single-model overconfidence.
- Decided by: Rob.
- Commit: `db25183`.

## 2026-05-15 · locked v2 architecture (calibrated ensemble forecaster, NOT multi-agent debate)

- Replaced the variant-dispatch placeholder forecaster with a locked seven-stage pipeline: `market_router -> retrieval_gate -> decomposition_forecaster -> ensemble_forecaster -> calibrator -> risk_gate`. The agent is market-aware (every market is routed by domain before forecasting), retrieval-disciplined (one to three high-credibility sources, no broad crawl), and uses median-of-logits ensembling with disagreement gating. The code, not any model, computes the final probability. We explicitly chose this over multi-agent debate, raw LLM confidence, broad web crawling, and fine-tuning. Locked risk constants (EDGE_THRESHOLD=0.08, MAX_TRADES_PER_TICK=3, etc.) carry forward unchanged. Full details and the JSON decomposition schema live in `docs/ARCHITECTURE_V2.md`.
- Decided by: Rob.
- Commit: this branch (`feat/2026-05-15-prophet-architecture-v2`).

## 2026-05-15 · v2 rebased on top of Gemini's BenchmarkSession lifecycle

- After Gemini's lifecycle PR merged to main, v2 was rebased so the v2 calibrated-decomposition pipeline runs INSIDE the BenchmarkSession + continuous-loop + `--once` infrastructure. The two PRs are orthogonal layers: Gemini owns control flow (claim/load/finalize/complete + graceful shutdown), v2 owns forecast quality (router → retrieval → decomposition → ensemble → calibrator).
- Conviction-floor (`|p_final - 0.5| >= 0.10`) preserved from Gemini's agreement-gate as a `meaningful_conviction` skip reason in v2's calibrator stage. Configurable via `PROPHET_CONVICTION_FLOOR`.
- Pipeline wrapped in try/except returning market-mid fallback; one failing stage never crashes the tick loop.
- Decision: keep both safety properties even though v2's ensemble disagreement penalty semantically subsumes the agreement gate.
- Decided by: Rob.
