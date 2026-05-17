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

## 2026-05-16 · forecasting-track scaffolding + first Brier baseline

- We're on the Prophet Hacks **forecasting track**, not the trading track. The CLI is `prophet forecast {register,events,predict,evaluate,retrieve,leaderboard}` and the scoring is Brier against actual outcomes per `ai_prophet_core.forecast.evaluate`.
- The Prediction contract is `predict(event: dict) -> {"p_yes": float ∈ [0.01, 0.99], "rationale": str}`. The binary YES condition per market is `resolved_outcome.value == [outcomes[0]]` — i.e. is the first listed outcome the winner.
- Built `forecast_track.py` with two variants exposing the official contract:
  - `predict_uniform_prior(event)` — deterministic `1/len(outcomes)`, no LLM cost.
  - `predict_single_llm(event)` — one Anthropic Sonnet 4.6 call per event.
- Built `scripts/build_actuals.py` to convert the resolved-events JSON into the `{"market_ticker": 1.0_or_0.0}` shape `prophet forecast evaluate` consumes.
- Built `scripts/backtest_forecast.py` to run variants, persist predictions, invoke the evaluator, and cross-check Brier locally.
- **First real numbers against `sample-resolved` (26 events, 12 YES / 14 NO, public dataset):**
  - random-0.5 reference: 0.250
  - `uniform_prior`: **0.219**
  - `single_llm` (Sonnet 4.6): **0.190**  ← +13% improvement on a real-data backtest
- Cost of the single-LLM run: ~95s wall time, well under $0.20. Plenty of budget to iterate with ensembles or stronger models.
- Decided by: Claude Code (correcting from the trading-track focus after Rob pointed at `prophetarena.co/developer` and `using_sample_datasets.md`).
- Commit: this branch.

## 2026-05-16 · risk.py imports from ai_prophet_core.ruleset; new caps wired

- `risk.py` now imports `ai_prophet_core.ruleset` as `_server` and asserts at import time that every one of our caps is at least as strict as the corresponding server cap. A programmer error (raising our cap above the server's) will be caught on first import rather than at first rejected intent.
- Three new server-mirrored constants: `MAX_TRADES_PER_DAY = 100` (server rolling 24h cap), `MAX_GROSS_EXPOSURE = 10_000.0` (server total exposure cap), `TICK_SUBMISSION_DEADLINE_SECS = 540` (server 9-min submission window). None of these existed previously and each represents a real way the live server could reject our intents.
- New helpers: `compute_gross_exposure(positions)` sums `shares * avg_entry_price` across open positions; `assert_under_gross_exposure(new_notional, current_gross)` and `assert_under_daily_trade_count(trades_in_last_24h)` mirror the existing assert-style helpers.
- Rationale: pre-kickoff read of `docs/build_a_bot.md` and `ai_prophet_core.ruleset` (in the forked `ai-prophet/ai-prophet` repo at `~/Desktop/ai-prophet/`) surfaced these caps as concrete server-side enforcement points we hadn't mirrored. The 5-min CLAUDE.md table is now wrong but the code is right; the table will be updated in the same commit.
- Decided by: Claude Code (pre-kickoff initiative; gaps confirmed against upstream SDK source).
- Commit: this branch.

## 2026-05-16 · agent.py: network-resilient claim_tick, finally pattern, put_plan, error_detail

- `agent.py` ported four resilience patterns from upstream `prophet-agent/agent.py`:
  1. **`_claim_tick_with_backoff`** — exponential backoff on transient network errors during `session.claim_tick()`. Base 30s, doubles, capped at 300s. A blackout exceeding 5 min escalates from WARN to ERROR so a long outage is visible.
  2. **`complete_tick` in a `finally` block** — guarantees the lease is released even if the tick body raises mid-forecast. Previously the lease could leak if `run_one_tick` raised between `claim_tick` and `complete_tick`.
  3. **`finalize(status="FAILED", error_detail=...)`** — passes the first 200 chars of the exception to the server so the experiment record has actual diagnostic context, not just `error_code="TICK_ERROR"`.
  4. **`session.put_plan(lease, idx, plan_json)`** — persists the per-tick decision list server-side so it shows up in `/experiments/{id}/reasoning` and the `prophet trade dashboard` view. Best-effort: a `put_plan` failure logs WARN and does not fail the tick. Free portfolio-artifact win.
- `run_one_tick` no longer calls `session.finalize` or `session.complete_tick` itself; both are owned by `run_continuous`'s try/except/finally wrapper. Return value is now `(summary, updated_lease)` so the wrapper has the post-`load_candidates` lease.
- `upsert_participant` now passes `rep=0` to match upstream convention; lets future variants run under one experiment.
- Gross-exposure check added inside the per-market risk gate: `assert_under_gross_exposure(notional, running_gross)`, where `running_gross` is the portfolio exposure at tick start plus any trades accepted so far in this tick.
- Rationale: same upstream read as the risk.py decision. The `finally` pattern is the single biggest survivability improvement — without it, one mid-tick exception leaks a lease and blocks the experiment for `lease_sec` (600s default). `put_plan` is the highest-portfolio-value low-effort addition.
- Decided by: Claude Code (pre-kickoff initiative).
- Commit: this branch.

## 2026-05-16 · agreement_gate float-precision pad

- `forecaster.agreement_gate` now uses a `_CONVICTION_EPSILON = 1e-9` pad so the exact-threshold boundary case (`abs(p - 0.5) == 0.10`) is admitted, matching Codex Goal 3's `>= 0.10` spec.
- Rationale: `abs(0.60 - 0.5)` evaluates to `0.09999999999999998` in float, so the naive `< 0.10` check incorrectly rejected the exact-bucket case `agreement_gate(0.60, 0.60)`. `test_exact_threshold` was failing as a result. Bucketed probabilities snap to `[0.10, 0.20, ..., 0.90]`, so exact-boundary inputs are a real and frequent case in practice.
- The pad lives on the LHS of the comparison, so the gate still cleanly rejects any input genuinely below the threshold (e.g., 0.55 → `abs - 0.5 + eps = 0.0500000001 < 0.10` → reject).
- Decided by: Claude Code (taking initiative; bug surfaced in pytest, spec was unambiguous).
- Commit: this branch.

## 2026-05-16 · pytest added to requirements.txt

- `pytest>=8.0,<9.0` added under a "Test runner" comment. `scripts/agent/verify.sh` was silently skipping `pytest tests/` because the dep wasn't installable from `requirements.txt`. Now `verify.sh` actually exercises the 47-test suite.
- Decided by: Claude Code (verify.sh gate is the merge signal; it can't be silently skipping tests).
- Commit: this branch.

## 2026-05-16 · python baseline bumped from 3.11 to 3.13

- `.python-version` now reads `3.13`. README and `docs/PRE_EVENT_CHECKLIST.md` updated to "3.11+ (3.13 is the dev baseline as of 2026-05-16)".
- Rationale: kickoff machine has Python 3.13 installed (no 3.11). Every dep in `requirements.txt` supports 3.13 (openai 2.x, anthropic 0.100+, pydantic 2.x, pandas 2.x, matplotlib 3.8+, pyyaml 6.x). The repo code uses `from __future__ import annotations` throughout and has no version-specific features. README's "3.11+" was always the real floor; the strict `.python-version` was the original dev environment, not a hard requirement.
- Trade-off: ai-prophet-core==0.1.4 was likely developed and tested against 3.11. If it breaks on 3.13, install 3.11 via `brew install python@3.11` then — fallback path is fast because we'd know the trigger.
- Decided by: Rob (delegated to take initiative; environment reality forced the choice).
- Commit: this branch.

## 2026-05-15 · multi-model agreement gate (stretch goal)

- Considered: only fire a trade when triage and forecast agree on direction (both YES-edge or both NO-edge).
- Status: deferred. Not in the skeleton. Promote to a separate forecaster variant once the single-model path is proven on a real tick.
- Decided by: Rob.
- Commit: not yet wired.

## 2026-05-16 · live docs aligned to Railway production endpoint

- README, `docs/STATUS.yaml`, `docs/STATUS.html`, `.env.example`, and `forecast_agent_server.py` now describe the current production path: Railway service `oracles-agent`, custom domain `agent.forecastingpath.com`, Prophet Arena endpoint-only submission, and `multi_outcome_retrieval` as the served variant.
- Added `docs/LIVE_OPERATIONS.md` as the handoff for deployment, health checks, public dashboard verification, and common incidents. This replaces stale cloudflared-tunnel instructions for production; Cloudflare quick tunnels remain explicitly documented as not active.
- Rationale: the repo had outgrown its trading-skeleton README and several status artifacts still said `multi_outcome` or quick tunnel. Future agents need one reliable source of current deployment truth before touching forecasting logic.
- Decided by: Codex orientation pass after Railway, GitHub PR, and Prophet Arena endpoint checks.
- Commit: this branch.

## 2026-05-16 · app root redirects to live dashboard

- `GET /` now redirects to `/dashboard` with HTTP 307. Once `forecastingpath.com` is bound to the Railway service, the apex domain will open the live monitor instead of a bare JSON API stub.
- Added direct FastAPI endpoint tests for root redirect, `/healthz`, `/predict` response shape, and `/favicon.ico`.
- Railway custom-domain creation for `forecastingpath.com` is still blocked from this shell by `Unauthorized. Please run railway login again.` Manual Railway UI plus Cloudflare DNS steps are documented in `docs/LIVE_OPERATIONS.md`.
- Decided by: Codex after confirming `agent.forecastingpath.com` works and apex `forecastingpath.com` lacks an A/AAAA/CNAME answer.
- Commit: this branch.

## 2026-05-16 · live dashboard moved behind token auth

- The dashboard, prediction-history JSON, and SSE stream now require `DASHBOARD_AUTH_TOKEN` when the variable is set. Auth accepts `?token=...`, `x-dashboard-token`, `Authorization: Bearer ...`, or the `dashboard_token` cookie set after a valid query-token visit.
- `GET /` is now a public status page instead of a redirect to the live monitor. `/predict` and `/healthz` remain public because Prophet Arena needs direct endpoint access and health checks should stay simple.
- FastAPI Swagger/OpenAPI routes are disabled on this app. The live monitor contains enough internals and a production prediction form that public access is not worth the competition leak/spend risk.
- Decided by: Codex after Rob asked whether the dashboard was public and whether it should be.
- Commit: this branch.

## 2026-05-16 · evaluation helpers hardened after review

- Added explicit validation for evaluation probabilities, outcomes, and bin counts so bad offline-eval inputs fail with `ValueError` instead of silently corrupting bins.
- Fixed simulated NO-contract payoff math to reject impossible zero-price contracts instead of producing huge fake returns.
- Rewrote `agent_protocol.md` to match the actual Railway production path and corrected the Brier gate to lower-is-better / positive BSS.
- Added focused tests for Brier/ECE validation, return math, leakage detection, and `/events` dashboard auth.
- Decided by: Codex after requested code review of commits `2e38088..b5ed6de`.
- Commit: this branch.

## 2026-05-16 · Opus 4.7 in `predict_multi_outcome_retrieval`

- The production forecast variant now calls `claude-opus-4-7` instead of `claude-sonnet-4-6`. `config.yaml` updated; Sonnet 4.6 prepended to the fallback chain.
- Rationale: branch smoke-test on a synthetic Chiefs/SB-LXI longshot showed Sonnet returned 0.25 (ignoring the +1500 / ~6% implied price the same Brave search surfaced), while Opus 4.7 returned 0.06 raw with the same evidence. Opus anchors to cited market odds materially better. Cost goes from ~$0.02 to ~$0.10/call; for hackathon volume the absolute cost is trivial vs the Brier upside.
- Companion change: the multi-outcome retrieval system prompt now has an explicit "market-odds anchoring" block — anchor to cited odds, move >0.05 only with specific contrary evidence.
- Decided by: Claude during Phase 2 work, authorized by Rob.
- Commit: `9652016`.

## 2026-05-16 · `longshot_guard_floor` capped at 0.10 (bug fix)

- Old formula `max(0.05, 0.5 / n_outcomes)` set the binary floor to 0.25, silently clamping every binary prediction into [0.25, 0.75]. New formula: `min(0.10, max(0.05, 0.5 / n_outcomes))`. 0.10 is the principled Kalshi-paper empirical threshold (sub-$0.10 contracts lose >60%).
- Discovered when the post-Opus-swap branch smoke returned 0.06 raw and the guard inflated it to 0.25, destroying ~0.06 of Brier on a single binary event. Per-event improvement on binary longshots is roughly 6x (0.0625 -> 0.0100).
- A unit test asserting `longshot_guard_floor(2) <= 0.10` would have caught this; backlogged.
- Decided by: Claude after smoke-test surfaced the clamp. Authorized by Rob.
- Commit: `9652016`.

## 2026-05-16 · `.claude/` and `proposed_retrieval/` gitignored to fix deploys

- `railway up` uploads all untracked files. Three deploys (`92c5c5f4`, `c897643c`, `872b769a`) failed because `.claude/worktrees/` was 18MB and either corrupted the upload (TLS BadRecordMac) or busted the build with no logs.
- Fix: gitignore `.claude/` and `proposed_retrieval/`. Subsequent deploy `415edc6e` succeeded cleanly. Smoke confirmed live: Knicks 2027 NBA Finals longshot returned 0.10 (was 0.25 on old code), proving Opus + new floor are running.
- Lesson: `du -sh` of what `railway up` would actually send should be a pre-deploy step. Added `scripts/preflight.sh` to formalize this.
- Decided by: Claude after diagnosing the deploy bloat.
- Commit: `a6cfcc7`.

## 2026-05-16 · CI/CD hardening — preflight, deploy wrapper, /healthz commit SHA

- `scripts/preflight.sh`: runs verify gate, checks working tree clean with no untracked non-ignored files, confirms HEAD = origin/main, sums tracked upload size (warns >10MB), surfaces deployed-SHA vs local HEAD.
- `scripts/agent/deploy.sh`: single safe path to deploy. Runs preflight, pins commit SHA to the non-secret Railway variable `PROPHET_BUILD_COMMIT_SHA`, calls `railway up --detach`. Use this instead of raw `railway up`.
- `forecast_agent_server.py:_build_commit_sha()`: reads `PROPHET_BUILD_COMMIT_SHA` first, then `RAILWAY_GIT_COMMIT_SHA`, then `.commit_sha`, then a `git rev-parse` fallback. Surfaced as `"commit"` field on `/healthz`. Now anyone (curl, Codex, future-Rob) can verify which code is live with a single GET.
- Rationale: today the question "is the deploy actually current?" cost ~1 hour of confusion. Each of these three hardens a specific failure mode from the day's incidents.
- Decided by: Claude, authorized by Rob ("All three now, before next PA call").
- Correction: first attempt pinned `.commit_sha`, but `railway up` did not upload that gitignored file and `/healthz.commit` returned `dev`. The env-var pin fixes this for file-upload deploys. Preflight also now blocks untracked files rather than merely counting them, because untracked files would make deployed artifacts differ from `origin/main`.
- Commit: this commit.

## 2026-05-16 · Phase 2 backtest: 40.7% relative Brier reduction (with caveats)

- Reran the 26-event sample-resolved backtest against current production code (Opus 4.7 + market-odds-anchor prompt + capped 0.10 longshot floor). Result: **mean Brier 0.0379** vs the previous Sonnet-4.6 baseline of 0.0639 — a **40.7% relative reduction** on this dataset.
- Snapshot of the Phase 1 (Sonnet) predictions saved at `data/predictions/multi_outcome_retrieval.phase1_sonnet.json`; new predictions at `data/predictions/multi_outcome_retrieval.json`; per-event diff at `reports/phase2_vs_phase1_backtest.json`.
- **Where the win came from (important honesty):** by outcome count, binary events (n=2, 14 of 26) drove almost the entire improvement: Brier 0.0879 → 0.0425 (Δ=−0.0454). All five of the top-5 wins are binary longshots where the old guard floored a confident-correct 0.05–0.10 prediction up to 0.25 (the floor bug). Opus 4.7's stronger anchoring is a secondary effect. Multi-outcome events were mixed — n=3 events improved (one big −0.0711), n=20 events regressed (+0.0459 average) because Opus puts more mass on outcome[0] than Sonnet did, which costs more when outcome[0] isn't the winner.
- **Don't overclaim:** 26 events is a tiny sample, and the dataset skews toward binary tennis matches where the longshot guard fix matters disproportionately. Live Prophet Arena events may have a different outcome-count distribution. Treat the 0.0379 number as "directionally validated, not converged."
- Decided by: Claude after Rob asked for real proof rather than vibes; spend ~$2.60 for the rerun.
- Commit: this commit.

## 2026-05-16 · Gemini 3.1 Pro Preview ablation — keep Opus 4.7

- Ran `google/gemini-3.1-pro-preview` through OpenRouter using the SAME `predict_multi_outcome_retrieval` pipeline (same Brave retrieval, same anchor prompt, same longshot floor) on the 26-event sample-resolved set.
- Result: **mean Brier 0.4149 vs Opus 4.7's 0.0379** — Gemini was ~11x worse on this dataset, dominated by catastrophic multi-outcome failures (multi-mean 0.8115). Inspection showed Gemini emitting malformed JSON on multi-outcome events (trailing commas, bogus keys like `"1"` instead of outcome labels) — our prompt's "use EXACT outcome labels supplied" instruction didn't land.
- Total spend: ~$0.44.
- **Important lesson:** Gemini 3 Pro is the public Prophet Arena leaderboard's #1 fixed-context model. That ranking is on PA's own harness, not ours. **Leaderboard #1 ≠ best in your pipeline.** Worth quoting in any "model selection" portfolio section.
- Decision: stay on Opus 4.7 for production. The ablation script `scripts/ablate_openrouter.py` is now the harness for any future "should we swap?" question — run it on the same 26 events before any model swap.
- Decided by: Claude, authorized by Rob to spend on ablation rather than vibes.
- Commit: this commit.

## 2026-05-16 · Multi-vendor ablation across 4 models — same pipeline, same prompt

Ran the same `predict_multi_outcome_retrieval` pipeline (Brave 5-chunk retrieval + market-anchor system prompt + 0.10 longshot floor) through 4 different LLMs on the 26-event sample-resolved set, swapping ONLY the LLM call. Spend: ~$2.50 total.

| Model | Mean Brier | Binary (n=14) | Multi (n=12) |
|---|---|---|---|
| **Opus 4.7 (production)** | **0.0379** | 0.0425 | 0.0177 |
| Claude Opus 4.6 | 0.2264 | 0.0438 | 0.4396 |
| GPT-5.2 | 0.2584 | 0.0538 | 0.4971 |
| Gemini 3.1 Pro Preview | 0.4149 | 0.0750 | 0.8115 |

**Key finding:** binary-event Brier is roughly comparable across all four models (0.04–0.08 range). The Opus 4.7 win is dominated by **multi-outcome JSON schema compliance**. Three of the four alternatives emit malformed JSON or assign probability mass to keys that aren't in the supplied outcome list, defaulting our parser to uniform prior — which then loses badly when the actual winner had high-prior support.

**What this means for production decisions:**
- Opus 4.7 is locked in for `multi_outcome_retrieval` (no swap)
- For a future binary-only variant, GPT-5.2 / Opus 4.6 are viable cheap alternatives
- The schema-compliance failure mode is a real risk if Opus 4.7 becomes deprecated. Mitigation: parse-resilience in `_parse_multi_outcome_json` (e.g. fuzzy match outcome labels, retry once on parse failure)
- "Leaderboard #1 ≠ best in your pipeline" — confirmed three different ways

Files: `data/predictions/ablation_*.json` (per-model predictions), `scripts/ablate_openrouter.py` (the standard ablation harness).

Decided by: Claude after Rob authorized aggressive spending for real ablation data ("do not screw me if you end up going easy").

## 2026-05-17 · GPT-5.5 ablation — confirms the schema-compliance hypothesis

- Ran `openai/gpt-5.5` through the same `predict_multi_outcome_retrieval` pipeline on the 26-event sample-resolved set via `scripts/ablate_openrouter.py`. Spend ~$1.20.
- Result: mean Brier **0.3226** (8.5× worse than Opus 4.7's 0.0379).
- Decomposition: binary mean 0.0376 (*slightly better* than Opus 4.7's 0.0425); multi-outcome mean 0.6552 (37× worse than Opus 4.7's 0.0177).
- Decision: do not swap. Same JSON schema failure mode we've now observed in 4 of 4 non-Anthropic-Opus-4.7 models (GPT-5.2, GPT-5.5, Opus 4.6, Gemini 3.1 Pro). The schema-compliance pattern is robust across the OpenAI/Google/older-Anthropic axis.
- Note for the workshop paper: GPT-5.5's competitive binary number is interesting — for a binary-only pipeline, it would be a viable cheaper alternative to Opus 4.7. PA's event mix is unknown but Discord ("events won't be highly skewed") suggests both shapes will appear.
- Decided by: Claude after Rob asked whether newer OpenAI models warranted a swap.
- Commit: this commit.

## 2026-05-17 · CRITICAL: scoring-methodology correction (single-binary vs multi-class)

A prompt-ablation pass surfaced a methodology inconsistency in the
multi-model comparison previously published in REPORT.md, FINDINGS.md,
README.md, and static/summary.html. The headline 0.0378 mean Brier for
production Opus 4.7 was correct **under PA's CLI scoring**, which
implements single-binary Brier on `(p_yes - 1{outcomes[0] won})²` —
verified by running `prophet forecast evaluate` against the same
predictions and confirming 0.037819. Our `backtest_forecast.py` local
computation also uses single-binary, matching the CLI.

But the cross-model ablation table compared Opus 4.7 at 0.0378
(single-binary) against alternatives at 0.22–0.42 (proper multi-class
Brier from `scripts/ablate_openrouter.py`, which sums squared error
across all outcomes per event). Apples-to-oranges; the dramatic 5–25×
"production wins" gap was inflated by the metric mismatch, not by
actual model performance.

**Consistent single-binary Brier across all six models on the same
26 resolved events:**

| Model | Single-binary | Proper multi-class | Multi-only (n=12) |
|---|---:|---:|---:|
| **Opus 4.7 (production)** | **0.0378** | 0.2558 | 0.4551 |
| Opus 4.6 | 0.0391 | **0.2500** | 0.4396 |
| GPT-5.2 | 0.0438 | 0.2874 | 0.4971 |
| Sonnet 4.6 (prev prod) | 0.0639 | 0.6912 | (no probs stored) |
| GPT-5.5 | 0.0920 | 0.3429 | 0.6552 |
| Gemini 3.1 Pro Preview | 0.0983 | 0.4773 | 0.8115 |

**Corrected claims:**

- Production Opus 4.7 wins single-binary Brier on this dataset, but
  by ~3.4% over Opus 4.6, not by 5×.
- The Phase 2 improvement (0.0639 → 0.0378 vs Sonnet) is real and
  has a paired-bootstrap CI of [0.0143, 0.0374] excluding zero.
- The Sonnet→Opus model swap accounts for ~15% of the gap; the
  longshot-floor bug fix accounts for ~85%. That decomposition stands.
- Under proper multi-class scoring (what PA's docs describe, though
  not what their CLI implements), Opus 4.6 is marginally better
  (0.2500 vs 0.2558). Worth flagging in the workshop paper as
  "model selection is metric-dependent on small n."
- The schema-compliance hypothesis applies most cleanly to Gemini
  and GPT-5.5 (worst under both metrics). For Opus 4.6 and GPT-5.2
  the multi-class gap to production is small enough that "schema
  compliance" is no longer the dominant explanation.

**Root cause of the script error:** `scripts/backtest_forecast.py:_predict_one`
stored only `p_yes` and `rationale`, dropping the per-outcome
`probabilities` array. This meant summary-report multi-class
recomputation fell back to `1/n` uniform per outcome, producing
misleadingly-low multi-Brier numbers. Fix: prediction record now
includes `probabilities` + `evidence_urls` fields. After fix,
re-running the production backtest yields identical single-binary
0.037819 plus proper multi-class 0.2558.

**What we will report going forward:**

- Single-binary Brier (PA CLI scoring) as the primary headline number,
  with the caveat that PA's published formula suggests multi-class
  and the actual live-eval scoring is ambiguous.
- Multi-class Brier as a secondary caveat number for completeness.
- Cross-model comparisons must use the same metric to be honest;
  prefer single-binary for the audience-facing table.

Decided by: Claude after surfacing the bug via prompt-ablation
discrepancy. Author of the original metric mismatch: also Claude;
postmortem is the discipline.
- Commit: this commit + the backtest script fix.
