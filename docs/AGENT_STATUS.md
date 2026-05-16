# Agent status board

Live state of every coding agent active on this repo. Each agent updates
its own section on the first turn and again on the last turn of a work
session.

Rule: if your section says you own `agent.py` or `config.yaml`, no other
agent may modify those files in the same session. Touch supporting modules
instead (`market_filter.py`, `forecaster.py`, `risk.py`, `retrieval/`, `prompts/`).

Format: one `## <agent name / worktree>` heading per agent, body has:
- **Current task:** one line
- **Files owned this session:** list of paths
- **Last updated:** ISO timestamp (UTC)
- **Notes:** optional, any blockers or hand-off context

---

## main (Rob, direct edits)

- **Current task:** idle.
- **Files owned this session:** none.
- **Last updated:** 2026-05-15T00:00:00Z (seed entry).
- **Notes:** Rob holds final-say on `agent.py`, `config.yaml`, and anything in `docs/`. Other agents propose via branches; Rob merges.

## codex/main-orientation

- **Current task:** Code-review follow-up for evaluation helpers and agent handoff docs.
- **Files owned this session:** `agent_protocol.md`, `evaluation/`, `forecasting/`, `scripts/watch_predictions.sh`, `tests/test_evaluation.py`, `tests/test_forecast_agent_server.py`, `docs/DECISIONS.md`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-16T19:19:43Z
- **Notes:** Review found no critical production-path regression. Important fixes are validation for evaluation metrics, return math for NO contracts, current-state handoff docs, and SSE auth coverage.
- **Heads-up (added 2026-05-16T19:58Z):** Your snapshot reading `main = a254665, deploy 1130735a SUCCESS` is now stale. Phase 2 has landed: commit `9652016` swaps `predict_multi_outcome_retrieval` to Opus 4.7, adds a market-odds anchoring block to its system prompt, and fixes a binary-clamp bug in `longshot_guard_floor` (was 0.25 for n=2, now capped at 0.10 = Kalshi threshold). Deploy `415edc6e` SUCCESS at 19:54Z; live smoke confirms (Knicks/SB 2027 returns 0.10, was 0.25). **Before any "current state" claim, run `git fetch && git log --oneline -5` AND `curl -s https://agent.forecastingpath.com/healthz | jq .commit` so we agree on reality.** From now on, deploys go via `./scripts/agent/deploy.sh` (preflight checks upload size; this would have prevented the 18MB `.claude/` worktree bloat that broke three of my deploys earlier).

## claude/phase-2-and-cicd

- **Current task:** Phase 2 deployed, backtest done, SAE modules pulled, Gemini ablation done, dashboard try-form overhaul shipped. Now handing the live-variant SAE wiring to Codex (see below).
- **Files owned this session:** `forecast_track.py`, `forecast_agent_server.py`, `config.yaml`, `scripts/preflight.sh`, `scripts/agent/deploy.sh`, `scripts/analyze_results.py`, `scripts/ablate_openrouter.py`, `forecasting/borrowed_strength.py`, `forecasting/sae_shrinkage.py`, `forecasting/reliability_tracking.py`, `forecasting/uncertainty.py`, `forecasting/domain_pools.py`, `tests/test_borrowed_strength.py`, `tests/test_sae_shrinkage.py`, `tests/test_forecast_agent_server.py`, `docs/HANDOFF.md`, `docs/DECISIONS.md`, `.gitignore`, `static/`.
- **Last updated:** 2026-05-16T21:05:00Z
- **Real measured wins this session:**
  - Phase 2 (Opus 4.7 + market-anchor prompt + 0.10 floor cap on `longshot_guard_floor`): backtest mean Brier **0.0379** vs Sonnet baseline 0.0639 (**40.7% relative reduction**). Source of the gain is mostly the floor-bug fix on binary events; Opus anchoring is secondary. Documented in DECISIONS.md.
  - Gemini 3.1 Pro Preview ablation through OpenRouter: 0.4149 mean Brier (~11x worse, multi-outcome catastrophic). Decision: stay on Opus 4.7. `scripts/ablate_openrouter.py` is now the standard harness for any "swap model?" question.
  - SAE modules from PR #4 cherry-picked as net-new files; 31 new tests pass; total 127.
  - CI/CD: preflight gate, deploy wrapper, `/healthz` commit SHA, dashboard polish (architecture image, brand fix, favicon, OG tags), commit SHA visible on `/` and `/dashboard`.
  - Dashboard try-form overhauled: example dropdown, description + rules fields, validation, latency display.

## codex/sae-variant-wire (HANDOFF — TODO)

- **Suggested task:** Build `predict_multi_outcome_retrieval_sae` variant in `forecast_track.py`. Drop-in alternative to current production variant using `forecasting/borrowed_strength.py:borrowed_strength_estimate()` for hierarchical shrinkage. Then run `scripts/backtest_forecast.py --variants multi_outcome_retrieval_sae` and compare to the current Brier 0.0379 baseline.
- **Why this matters now:** Phase 2 backtest decomposition (see DECISIONS.md 2026-05-16 entries) shows multi-outcome events are where Opus regresses vs Sonnet on n=20 events. SAE shrinkage is specifically designed to fix multi-outcome calibration via domain-level Fay-Herriot effects. This is the empirical test.
- **Files to touch:** new function in `forecast_track.py` (add to `_VARIANT_FN` map, `_VARIANT_COSTS`, `_VARIANT_DESCRIPTIONS` in `forecast_agent_server.py`). Reuse `_build_query`, `_brave_search`, `_dedupe_by_domain`, `_build_retrieval_user_prompt`, `_MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT`, `apply_longshot_guard`. The new step is calling `borrowed_strength_estimate()` on the per-outcome probabilities.
- **Gotchas:** `borrowed_strength_estimate()` expects `p_market` per outcome — we don't have market prices in the /predict path. Options: (a) skip the market-prior term and use uniform 1/n as the prior, (b) try to parse market odds from the Brave evidence snippets (brittle), (c) just use the model-disagreement + domain-shrinkage parts and skip market_prior. (a) or (c) is the honest path.
- **Don't:** change config.yaml or PROPHET_AGENT_VARIANT env on Railway. New variant is offline-only for testing first. Decision to promote it to production only after backtest shows improvement.
- **Verify with:** `./scripts/agent/verify.sh` must stay green, `scripts/preflight.sh` must pass before deploying.

## codex/cicd-healthz-fix

- **Current task:** Fix `/healthz.commit` returning `dev` after the first deploy-wrapper implementation.
- **Files owned this session:** `forecast_agent_server.py`, `scripts/agent/deploy.sh`, `scripts/preflight.sh`, `.gitignore`, `.env.example`, `tests/test_forecast_agent_server.py`, `docs/DECISIONS.md`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-16T20:09:08Z
- **Notes:** Root cause: `.commit_sha` is gitignored and did not survive `railway up`; Railway did not provide `RAILWAY_GIT_COMMIT_SHA` for file-upload deploys. Fix pins `PROPHET_BUILD_COMMIT_SHA` via Railway variable before deploy and makes preflight reject untracked non-ignored files.

## (template) <agent or worktree name>

- **Current task:** `<one line>`
- **Files owned this session:** `<paths>`
- **Last updated:** `<ISO timestamp>`
- **Notes:** `<optional>`
