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

## codex/live-frontend-observatory

- **Current task:** Completed public/private frontend split: `/` is now a restrained public status page, `/observatory` is the PIN-gated research and operations console.
- **Files owned this session:** `forecast_agent_server.py`, `tests/test_forecast_agent_server.py`, `tests/test_pin_auth.py`, `docs/AGENT_STATUS.md`.
- **Last updated:** 2026-05-17T02:04:09Z
- **Notes:** No production variant, Railway env var, `static/`, `submission/`, `docs/DECISIONS.md`, or `docs/FINDINGS.md` changes. Public `/` intentionally omits exact model names, retrieval vendor, scoring formulas, GPT/Gemini comparison, and source links during active scoring. Internal `/observatory` keeps the live commit, variant, prediction count, first-call watch, GPT-5.5 answer, experiment board, and adversarial-review notes behind dashboard auth. Verified by focused auth/frontend tests, full `pytest tests/`, local content scrub check, and `./scripts/agent/verify.sh`.

## codex/persistent-observatory

- **Current task:** Completed disk-backed prediction history plus observatory trace table; Railway production volume is mounted.
- **Files owned this session:** `forecast_agent_server.py`, `tests/test_forecast_agent_server.py`, `scripts/agent/deploy.sh`, `docs/AGENT_STATUS.md`.
- **Last updated:** 2026-05-17T02:43:28Z
- **Notes:** `/predict` now appends each served prediction to ignored JSONL storage (`PROPHET_PREDICTION_STORE_PATH`, Railway volume path, then `logs/live_predictions.jsonl`). `/predictions` lazily reloads that store if the in-memory ring is empty, so a process restart no longer erases the visible recent trace history when the file remains available. `/observatory` now shows recent persisted predictions with probabilities, total latency, parse path, and warnings. `scripts/agent/deploy.sh` now deploys a minimal runtime bundle after preflight to avoid the repeated full-repo Railway code-snapshot/TLS upload failures. Railway production volume `oracles-agent-volume` (`1ae021a5-353d-4917-a474-8a5e0aa6dced`) is mounted on `oracles-agent` at `/data`; latest deployment `0b16e0f5` is `SUCCESS` and shows `volumeMounts: ["/data"]`. No production variant, public landing copy, `static/`, `submission/`, `docs/DECISIONS.md`, `docs/FINDINGS.md`, or `chat_completions_adapter.py` changed. Verified by new failing-first tests, full `pytest tests/`, local persisted-row render check, `./scripts/agent/verify.sh`, Railway volume list, Railway status, and live `/healthz`.

## codex/handoff-todo-list (rev. 2026-05-17T00:10Z)

Concrete asks for Codex. Items 1-5 from the prior list are DONE. This
revision is the new working list ordered by impact-per-effort.

**Before any claim about state, refresh:**
```
git fetch && git log --oneline -8
curl -s https://agent.forecastingpath.com/healthz | jq .commit
```

### Boundaries — strict (per Rob)

**Do not modify:**
- `submission/` (REPORT.md + PROJECT_STORY.md — Rob's hackathon
  artifacts, freshly rewritten in `6cd47f1`; mistakes here cost the
  submission)
- `docs/DECISIONS.md` (append-only; only ADD dated entries, never
  rewrite existing ones)
- `docs/FINDINGS.md` (Rob's research-findings doc; do not edit
  without explicit ask)
- `static/` (committed deploy assets)
- `chat_completions_adapter.py` (just shipped; treat as stable)
- Any production env var on Railway (`DASHBOARD_PIN`,
  `DASHBOARD_AUTH_TOKEN`, `PROPHET_AGENT_VARIANT`,
  `PROPHET_BUILD_COMMIT_SHA`)
- The `multi_outcome_retrieval` production variant — do not swap
  without a measured backtest win + Rob's OK

**Tone bar (for any text artifact you write):**
- No marketing-style AI language ("crushed it", "AI-powered",
  "agent collaboration", emoji)
- No speculative claims about Rob's background; stick to what's
  documented in commits/memory
- Stanford-bar register: methodological honesty, quantitative claims
  with provenance, acknowledged limitations
- If unsure whether a tone fits, default to terse + factual

### Tasks (do these)

**1. SSE streaming demo on /dashboard.** Pattern:
`POST /demo/start` → `GET /demo/stream/:run_id` (SSE) → `GET /demo/result/:run_id`.
Synthetic event triggers the real pipeline; stage-by-stage updates
stream to a small console panel. Uses the existing SSE infrastructure
on `/events`. ~2-3 hrs. **Touches `forecast_agent_server.py`.**
Coordinate via this doc before you start.

**2. After first PA call lands.** Inspect `/predictions` to see the
actual webhook event shape PA sends. Confirm `outcomes` field is
present (a Haiku safety net handles the case where it isn't, but
verifying live shape is critical). Record `trace.latency_ms.total` per
call — we have a 600s budget; if any approaches 60s, investigate.

**3. Paired-bootstrap CI on the headline Brier delta.** n=26 is small;
the Opus 4.7 vs Sonnet 4.6 difference (0.0379 vs 0.0639) deserves a
confidence interval. ~30 min via numpy in `scripts/`. Quote-able
addition for the next REPORT revision.

**4. Re-decompose the headline.** What fraction of the Phase 2 win is
the floor-bug fix vs the model swap? Re-run the previous Sonnet
pipeline with the new `min(0.10, max(0.05, 0.5/n))` floor and report.
Estimated cost ~$0.50. Adds rigor to `docs/FINDINGS.md` §8 item 2.

**5. Watch for PA scoring API readiness.** Once the eval window
opens, PA will publish resolved outcomes somewhere. When it does, run
`scripts/analyze_results.py` to compute live Brier vs the public
leaderboard. Document the gap in `docs/DECISIONS.md`.

**6. Brave reliability monitor.** ~~DONE in `baba9b0`.~~
`scripts/brave_health.sh` ships, exits 0/1/2 for ok/degraded/unhealthy,
parses Brave's `x-ratelimit-policy` + `x-ratelimit-remaining` headers
correctly (the `0` slot is "no cap" on the AI Data tier, not
exhaustion). **Open follow-up:** wire it into `scripts/full_check.sh`
as step 11, and ideally into a launchd / cron job that fires hourly
during the eval window.

**7. Test coverage gaps (real ones, not theoretical):**
   - `_distribute_p_yes_to_outcomes` (legacy binary adapter used by
     `single_llm`, `opus_47`, `opus_46` variants) — has a basic happy-path
     test but no edge cases (zero outcomes, single outcome, very-low p_yes,
     duplicate outcome labels).
   - Ensemble variants `predict_ensemble_logit` and
     `predict_ensemble_leaderboard` — exist in `forecast_track.py`,
     not in production routing, no tests beyond import.
   - `predict_hybrid_routed` — exists, no edge tests for the binary↔multi
     routing boundary (n=2 with weird labels, n=3 hitting both paths).
   - `predict_multi_outcome_retrieval_sae` (Codex's offline variant) —
     has `tests/test_forecast_track_sae.py` but tests are integration-
     style; no unit tests for the shrinkage math itself.
   - `chat_completions_adapter.py` — 16 tests cover the happy paths;
     untested: streaming attempts with malformed body, very-long messages
     past Anthropic context, attempted tool use, role='function' messages.
   - `forecast_agent_server.py:predict()` handler — covered for happy
     path + edge cases via `tests/test_predict_edge_cases.py`, but
     `_PREDICTION_HISTORY` ring buffer behavior (50-record cap, eviction
     order) is not explicitly tested.
   - Pipeline trace fields — populated correctly per smoke, but no
     test that asserts every field is present after a successful call.

**8. Real failure modes not currently monitored beyond Brave:**
   - Anthropic rate-limit / quota exhaustion (would fall through to
     uniform; no monitor)
   - In-memory `_PREDICTION_HISTORY` resets on every Railway restart
     (observability gap; not Brier-affecting)
   - Live commit drift from `main` (mitigated by `/healthz.commit` but
     manual check)
   - OpenRouter quota for ablations (only matters if we re-run them)

### Active state (refresh before claiming!)

- main: refresh before claiming with `git fetch && git log --oneline -1`
- production live commit: refresh with `curl -s https://agent.forecastingpath.com/healthz | jq .commit`
- variant: `multi_outcome_retrieval` (Opus 4.7 + market-anchor + 0.10 floor)
- tests: 253 passing after static-report auth coverage, private review brief coverage, and dashboard first-call triage coverage
- session spend: ~$13 of "100s" budget
- watcher: PID 33661, ~5h uptime, no PA activity yet
- open PRs on GitHub: 0 as of 2026-05-17T02:51Z. Recently merged: #5 SSE pipeline demo, #6 paired Brier bootstrap CI, #7 Brave health in full check, #8 hybrid routing edge tests, #9 ensemble variant tests, #11 observatory design spec, plus side-by-side static galleries on main.
- public exposure note: root stays sparse. Research/report HTML under `/static` (`summary.html`, `status.html`, `gallery_open.html`, `gallery_resolved.html`) is now dashboard-auth gated; public assets remain public.
- private UX note: `/dashboard` links the auth-gated `/review` judge brief, observatory, summary report, resolved gallery, open-event gallery, `/compare`, and `/compare-open` from a single "Private research views" section. `/review` is the operator/judge script: current proof, demo order, likely questions, and first-call checklist.
- first-call triage note: `/dashboard` now has a dedicated "First-call triage" block. Before PA activity it shows the inspection checklist; after a prediction enters memory it summarizes event, outcome count, total latency, parse path, warnings, and evidence URL count.
- self-critique ablation note: do **not** promote the two-pass reviewer to production. Initial run improved single-binary Brier by `-0.00293`; fresh replication at `a63d826c` regressed by `+0.00274`. `/static/summary.html` now reports both runs as an experimental replication log, not as a settled win.
- gallery scoring note: parse/API failures now render as striped `err` cells. Resolved-event means still count the uniform-prior fallback because that is the model's actual through-pipeline behavior; open-event spread excludes failed parses because there are no actuals yet and fallback values are not useful disagreement signals.
- PA hackathon submission: registered, team KODWBT, forecast check passed (PA's own form returned 200 + valid 4-outcome response)
- PA general onboarding (prophetarena.co/onboarding): verify current status before claiming; `/v1/chat/completions` shim is live with auth.

## codex/full-check-brave

- **Current task:** Brave Search reliability monitor wired into `scripts/full_check.sh`.
- **Files owned this session:** `scripts/full_check.sh`, `tests/test_full_check_script.py`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-17T00:06:29Z
- **Notes:** Worktree branch `.claude/worktrees/codex-full-check-brave` / `codex/full-check-brave`. Adds full-check step 5 for `scripts/brave_health.sh --quiet`; later checks renumbered to 11 total. Verified with shell syntax check, focused test, full pytest, and `./scripts/agent/verify.sh`.

## codex/bootstrap-ci

- **Current task:** Paired-bootstrap CI plus Phase 2 decomposition implemented and verified.
- **Files owned this session:** `scripts/bootstrap_brier_ci.py`, `scripts/backtest_forecast.py`, `tests/test_bootstrap_brier_ci.py`, `tests/test_backtest_forecast.py`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-17T00:04:29Z
- **Notes:** Worktree branch `.claude/worktrees/codex-bootstrap-ci` / `codex/bootstrap-ci`. Result on existing snapshots: Opus Phase 2 Brier 0.037912 vs Sonnet Phase 1 0.063939, mean improvement 0.026027, 95% paired-bootstrap CI [0.014270, 0.037373] from 50,000 resamples, seed 20260516. Local Sonnet rerun with the current floor scored 0.041838: floor fix accounts for 0.022100/0.026027 (~85%) of the headline improvement; Opus vs current-floor Sonnet accounts for 0.003927 (~15%) with 95% CI [-0.004346, 0.015081]. Reports written under ignored `reports/`. Also fixed `scripts/backtest_forecast.py` to use a PATH `prophet` CLI when a worktree has no local `.venv/`.

## codex/ensemble-tests

- **Current task:** Add non-production tests for `predict_ensemble_logit` and `predict_ensemble_leaderboard`.
- **Files owned this session:** `tests/test_ensemble_variants.py`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-17T00:46:06Z
- **Notes:** Worktree branch `.claude/worktrees/codex-ensemble-tests` / `codex/ensemble-tests`. Avoids production `multi_outcome_retrieval`, Railway env vars, `forecast_agent_server.py`, `static/`, `submission/`, `docs/DECISIONS.md`, `docs/FINDINGS.md`, and `chat_completions_adapter.py`.

## codex/hybrid-routing-tests

- **Current task:** Add non-production edge tests for `predict_hybrid_routed` routing and fix its one-outcome edge case.
- **Files owned this session:** `forecast_track.py`, `tests/test_hybrid_routing.py`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-17T00:17:04Z
- **Notes:** Worktree branch `.claude/worktrees/codex-hybrid-routing-tests` / `codex/hybrid-routing-tests`. Does not touch production `multi_outcome_retrieval`, Railway env vars, `static/`, `submission/`, `docs/DECISIONS.md`, `docs/FINDINGS.md`, or `chat_completions_adapter.py`.

## codex/sae-variant-wire (HANDOFF — TODO, see codex/handoff-todo-list above)

- See item 2 in the handoff list. This entry kept for backwards reference; the consolidated list is authoritative.

## codex/sse-demo

- **Current task:** PIN-protected dashboard SSE demo implemented and verified: `POST /demo/start` -> `GET /demo/stream/{run_id}` -> `GET /demo/result/{run_id}`.
- **Files owned this session:** `forecast_agent_server.py`, `tests/test_forecast_agent_server.py`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-16T23:57:38Z
- **Notes:** Worktree branch `.claude/worktrees/codex-sse-demo` / `codex/sse-demo`. Verified by focused SSE tests, full `pytest tests/ -q` (218 passed), `./scripts/agent/verify.sh`, and local browser smoke. Respect strict bounds: no `submission/`, `docs/DECISIONS.md`, `docs/FINDINGS.md`, `static/`, `chat_completions_adapter.py`, Railway env vars, or production variant changes.

## codex/observatory-design

- **Current task:** Design-only branch for the next auth-gated Observatory surface: live health, prediction traces, experiment matrix, adversarial review, and public/private visibility split.
- **Files owned this session:** `docs/superpowers/specs/2026-05-16-forecastingpath-observatory-design.md`, `.gitignore`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-17T01:58:00Z
- **Notes:** No production code, `static/`, submission artifacts, research findings, decision log, Railway env vars, or production variant touched. Public `/` currently reveals exact model, variant, retrieval recipe, longshot formula, commit SHA, and failure-mode details; design recommends moving those details behind PIN during active scoring. GPT-5.5 was tried and is not a production swap: all-event single-binary Brier 0.0920 vs Opus 4.7 0.0378. `docs/WORKSHOP_PAPER_DRAFT.md` still has stale pre-correction metric claims and should be handled by the content owner.

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
