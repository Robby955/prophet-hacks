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

## codex/handoff-todo-list (2026-05-16T22:35Z)

Concrete asks for Codex when they pick up. Ordered by impact-per-effort.
First refresh state before any claim: `git fetch && git log --oneline -8`
and `curl -s https://agent.forecastingpath.com/healthz | jq .commit`.

**1. Verify the landing-page deploy landed.** Expected `/healthz.commit = e8c1beb9` (or newer). If it shows older, `./scripts/agent/deploy.sh "re-deploy landing"`.

**2. Wire SAE variant `predict_multi_outcome_retrieval_sae`** (the original handoff). All inputs ready:
   - Modules: `forecasting/borrowed_strength.py` (Fay-Herriot, domain effects, market prior), `forecasting/sae_shrinkage.py`, `forecasting/reliability_tracking.py`, `forecasting/uncertainty.py`, `forecasting/domain_pools.py` — all in main since `aedde18b`.
   - Pattern: new function in `forecast_track.py` reusing `_build_query`, `_brave_search`, `_dedupe_by_domain`, `_build_retrieval_user_prompt`, `_MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT`, `apply_longshot_guard`. Insert `borrowed_strength_estimate()` between LLM parse and longshot guard.
   - Gotcha: `borrowed_strength_estimate()` needs `p_market` per outcome. We don't have market prices in `/predict`. Honest path: skip market-prior term, use 1/n as the prior, keep just model-disagreement + domain-shrinkage. Don't try to parse odds from Brave snippets.
   - Validate: register in `_VARIANT_FN` map + `_VARIANT_COSTS` + `_VARIANT_DESCRIPTIONS`. Run `scripts/backtest_forecast.py --variants multi_outcome_retrieval_sae`. Compare to 0.0379 baseline. If improves multi-outcome Brier specifically (where Opus regresses), promote.
   - Don't: change `PROPHET_AGENT_VARIANT` on Railway. New variant is offline-only for testing first.

**3. Multi-vendor ablation on open events.** Currently `/compare` covers only the 26-event resolved set. Run `scripts/ablate_openrouter.py` on each open dataset (`sample-economics/-entertainment/-sports` already pulled to `data/datasets/`) for Opus 4.6 + GPT-5.2 + Sonnet 4.6. Cost ~$8 total. Then extend `/compare-open` to show all-model agreement matrix per event — high disagreement = high-information events to flag.

**4. Reliability diagram on `/compare`.** `evaluation/ece.py:reliability_diagram_data()` already returns the bin data we need. Add an SVG calibration curve at the top of `/compare` showing the production model's reliability across all resolved events. Honest about small-n caveats.

**5. PRs #1 + #4 housekeeping.** Both still open. Leave a comment on each summarizing what was selectively merged (PR #4 obs-slice + SAE shrinkage), what was rejected and why (PR #1's decomposition.py, PR #4's connectors stubs). Don't close unless Rob OKs.

**6. Compute-use / SSE streaming demo.** Rob asked for a "moodspan.org-quality" live demo where pipeline stages stream to the UI. Pattern: `POST /demo/start → GET /demo/stream/:run_id (SSE) → GET /demo/result/:run_id`. Synthetic event + real pipeline. Use the existing SSE infrastructure on `/events`. ~3 hrs of work; flag if descoping.

**7. After first PA call lands.** Inspect `/predictions` to see the actual webhook event shape PA sends — confirm `outcomes` field is present (we built a Haiku safety net for the case where it isn't, but verifying live shape is critical). Check `trace.latency_ms.total` per call to confirm we're nowhere near the 10-min/event timeout.

**8. Don't touch.** `DASHBOARD_PIN` env var (Rob's), `PROPHET_BUILD_COMMIT_SHA` (set by deploy script), `multi_outcome_retrieval` variant in production (don't swap without a measured win), the cherry-picked `forecasting/*` and `evaluation/*` modules (stable now). Anything in `static/` (committed assets).

**Active state (refresh before claiming!):**
- main: `e8c1beb9` (after landing-page deploy lands)
- production: `multi_outcome_retrieval` (Opus 4.7 + market-anchor + 0.10 floor)
- watcher: PID 33661, ~3.5h uptime, no PA activity yet
- backtest Brier: 0.0379 (40.7% better than Sonnet baseline 0.0639)
- ~177 tests passing
- spend this session: ~$10

## codex/sae-variant-wire (HANDOFF — TODO, see codex/handoff-todo-list above)

- See item 2 in the handoff list. This entry kept for backwards reference; the consolidated list is authoritative.

## codex/sae-variant

- **Current task:** Offline SAE variant, reliability diagram, and open-event agreement matrix shipped to main and deployed. Cleaning stale longshot-floor docs/scripts found after deploy.
- **Files owned this session:** `forecast_track.py`, `forecast_agent_server.py`, `scripts/backtest_forecast.py`, `scripts/composite_score.py`, `tests/test_forecast_track_sae.py`, `tests/test_forecast_agent_server.py`, `tests/test_composite_score.py`, `data/predictions/ablation_open_*.json`, `docs/AGENT_STATUS.md`, `docs/LIVE_OPERATIONS.md`, `docs/STATUS.yaml`, `docs/STATUS.html`
- **Last updated:** 2026-05-16T22:01:48Z
- **Notes:** Commit `5116d34` is live on Railway with `/healthz.commit=5116d345`. `multi_outcome_retrieval_sae` backtest scored Brier `0.11567` vs production `0.03791`, so it is not a promotion candidate. Production `PROPHET_AGENT_VARIANT` remains `multi_outcome_retrieval`. Follow-up fix aligns `scripts/composite_score.py` and ops docs with the current `min(0.10, max(0.05, 0.5/n))` floor.

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
