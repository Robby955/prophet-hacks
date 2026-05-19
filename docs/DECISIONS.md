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
- Decision: switch focus to forecasting after checking `prophetarena.co/developer` and `using_sample_datasets.md`.
- Commit: this branch.

## 2026-05-16 · risk.py imports from ai_prophet_core.ruleset; new caps wired

- `risk.py` now imports `ai_prophet_core.ruleset` as `_server` and asserts at import time that every one of our caps is at least as strict as the corresponding server cap. A programmer error (raising our cap above the server's) will be caught on first import rather than at first rejected intent.
- Three new server-mirrored constants: `MAX_TRADES_PER_DAY = 100` (server rolling 24h cap), `MAX_GROSS_EXPOSURE = 10_000.0` (server total exposure cap), `TICK_SUBMISSION_DEADLINE_SECS = 540` (server 9-min submission window). None of these existed previously and each represents a real way the live server could reject our intents.
- New helpers: `compute_gross_exposure(positions)` sums `shares * avg_entry_price` across open positions; `assert_under_gross_exposure(new_notional, current_gross)` and `assert_under_daily_trade_count(trades_in_last_24h)` mirror the existing assert-style helpers.
- Rationale: pre-kickoff read of `docs/build_a_bot.md` and `ai_prophet_core.ruleset` (in the forked `ai-prophet/ai-prophet` repo at `~/Desktop/ai-prophet/`) surfaced these caps as concrete server-side enforcement points we had not mirrored. The early coordination table was stale but the code is right; the table will be updated in the same commit.
- Decision: pre-kickoff SDK review confirmed the gaps against upstream source.
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
- Decision: pre-kickoff upstream resilience review.
- Commit: this branch.

## 2026-05-16 · agreement_gate float-precision pad

- `forecaster.agreement_gate` now uses a `_CONVICTION_EPSILON = 1e-9` pad so the exact-threshold boundary case (`abs(p - 0.5) == 0.10`) is admitted, matching the `>= 0.10` gate.
- Rationale: `abs(0.60 - 0.5)` evaluates to `0.09999999999999998` in float, so the naive `< 0.10` check incorrectly rejected the exact-bucket case `agreement_gate(0.60, 0.60)`. `test_exact_threshold` was failing as a result. Bucketed probabilities snap to `[0.10, 0.20, ..., 0.90]`, so exact-boundary inputs are a real and frequent case in practice.
- The pad lives on the LHS of the comparison, so the gate still cleanly rejects any input genuinely below the threshold (e.g., 0.55 → `abs - 0.5 + eps = 0.0500000001 < 0.10` → reject).
- Decision: bug surfaced in pytest; the threshold spec was unambiguous.
- Commit: this branch.

## 2026-05-16 · pytest added to requirements.txt

- `pytest>=8.0,<9.0` added under a "Test runner" comment. `scripts/agent/verify.sh` was silently skipping `pytest tests/` because the dep wasn't installable from `requirements.txt`. Now `verify.sh` actually exercises the 47-test suite.
- Decision: `verify.sh` is the merge signal, so it cannot silently skip tests.
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
- Decision: orientation pass after Railway, GitHub PR, and Prophet Arena endpoint checks.
- Commit: this branch.

## 2026-05-16 · app root redirects to live dashboard

- `GET /` now redirects to `/dashboard` with HTTP 307. Once `forecastingpath.com` is bound to the Railway service, the apex domain will open the live monitor instead of a bare JSON API stub.
- Added direct FastAPI endpoint tests for root redirect, `/healthz`, `/predict` response shape, and `/favicon.ico`.
- Railway custom-domain creation for `forecastingpath.com` is still blocked from this shell by `Unauthorized. Please run railway login again.` Manual Railway UI plus Cloudflare DNS steps are documented in `docs/LIVE_OPERATIONS.md`.
- Decision: `agent.forecastingpath.com` works and apex `forecastingpath.com` lacks an A/AAAA/CNAME answer.
- Commit: this branch.

## 2026-05-16 · live dashboard moved behind token auth

- The dashboard, prediction-history JSON, and SSE stream now require `DASHBOARD_AUTH_TOKEN` when the variable is set. Auth accepts `?token=...`, `x-dashboard-token`, `Authorization: Bearer ...`, or the `dashboard_token` cookie set after a valid query-token visit.
- `GET /` is now a public status page instead of a redirect to the live monitor. `/predict` and `/healthz` remain public because Prophet Arena needs direct endpoint access and health checks should stay simple.
- FastAPI Swagger/OpenAPI routes are disabled on this app. The live monitor contains enough internals and a production prediction form that public access is not worth the competition leak/spend risk.
- Decision: dashboard access reviewed after Rob asked whether it was public and whether it should be.
- Commit: this branch.

## 2026-05-16 · evaluation helpers hardened after review

- Added explicit validation for evaluation probabilities, outcomes, and bin counts so bad offline-eval inputs fail with `ValueError` instead of silently corrupting bins.
- Fixed simulated NO-contract payoff math to reject impossible zero-price contracts instead of producing huge fake returns.
- Rewrote `agent_protocol.md` to match the actual Railway production path and corrected the Brier gate to lower-is-better / positive BSS.
- Added focused tests for Brier/ECE validation, return math, leakage detection, and `/events` dashboard auth.
- Decision: requested code review of commits `2e38088..b5ed6de`.
- Commit: this branch.

## 2026-05-16 · Opus 4.7 in `predict_multi_outcome_retrieval`

- The production forecast variant now calls `claude-opus-4-7` instead of `claude-sonnet-4-6`. `config.yaml` updated; Sonnet 4.6 prepended to the fallback chain.
- Rationale: branch smoke-test on a synthetic Chiefs/SB-LXI longshot showed Sonnet returned 0.25 (ignoring the +1500 / ~6% implied price the same Brave search surfaced), while Opus 4.7 returned 0.06 raw with the same evidence. Opus anchors to cited market odds materially better. Cost goes from ~$0.02 to ~$0.10/call; for hackathon volume the absolute cost is trivial vs the Brier upside.
- Companion change: the multi-outcome retrieval system prompt now has an explicit "market-odds anchoring" block — anchor to cited odds, move >0.05 only with specific contrary evidence.
- Decision: Phase 2 model swap, authorized by Rob.
- Commit: `9652016`.

## 2026-05-16 · `longshot_guard_floor` capped at 0.10 (bug fix)

- Old formula `max(0.05, 0.5 / n_outcomes)` set the binary floor to 0.25, silently clamping every binary prediction into [0.25, 0.75]. New formula: `min(0.10, max(0.05, 0.5 / n_outcomes))`. 0.10 is the principled Kalshi-paper empirical threshold (sub-$0.10 contracts lose >60%).
- Discovered when the post-Opus-swap branch smoke returned 0.06 raw and the guard inflated it to 0.25, destroying ~0.06 of Brier on a single binary event. Per-event improvement on binary longshots is roughly 6x (0.0625 -> 0.0100).
- A unit test asserting `longshot_guard_floor(2) <= 0.10` would have caught this; backlogged.
- Decision: smoke test surfaced the clamp; fix authorized by Rob.
- Commit: `9652016`.

## 2026-05-16 · `.claude/` and `proposed_retrieval/` gitignored to fix deploys

- `railway up` uploads all untracked files. Three deploys (`92c5c5f4`, `c897643c`, `872b769a`) failed because `.claude/worktrees/` was 18MB and either corrupted the upload (TLS BadRecordMac) or busted the build with no logs.
- Fix: gitignore `.claude/` and `proposed_retrieval/`. Subsequent deploy `415edc6e` succeeded cleanly. Smoke confirmed live: Knicks 2027 NBA Finals longshot returned 0.10 (was 0.25 on old code), proving Opus + new floor are running.
- Lesson: `du -sh` of what `railway up` would actually send should be a pre-deploy step. Added `scripts/preflight.sh` to formalize this.
- Decision: deploy bloat diagnosis.
- Commit: `a6cfcc7`.

## 2026-05-16 · CI/CD hardening — preflight, deploy wrapper, /healthz commit SHA

- `scripts/preflight.sh`: runs verify gate, checks working tree clean with no untracked non-ignored files, confirms HEAD = origin/main, sums tracked upload size (warns >10MB), surfaces deployed-SHA vs local HEAD.
- `scripts/agent/deploy.sh`: single safe path to deploy. Runs preflight, pins commit SHA to the non-secret Railway variable `PROPHET_BUILD_COMMIT_SHA`, calls `railway up --detach`. Use this instead of raw `railway up`.
- `forecast_agent_server.py:_build_commit_sha()`: reads `PROPHET_BUILD_COMMIT_SHA` first, then `RAILWAY_GIT_COMMIT_SHA`, then `.commit_sha`, then a `git rev-parse` fallback. Surfaced as `"commit"` field on `/healthz`. Operators can verify which code is live with a single GET.
- Rationale: today the question "is the deploy actually current?" cost ~1 hour of confusion. Each of these three hardens a specific failure mode from the day's incidents.
- Decision: authorized by Rob ("All three now, before next PA call").
- Correction: first attempt pinned `.commit_sha`, but `railway up` did not upload that gitignored file and `/healthz.commit` returned `dev`. The env-var pin fixes this for file-upload deploys. Preflight also now blocks untracked files rather than merely counting them, because untracked files would make deployed artifacts differ from `origin/main`.
- Commit: this commit.

## 2026-05-16 · Phase 2 backtest: 40.7% relative Brier reduction (with caveats)

- Reran the 26-event sample-resolved backtest against current production code (Opus 4.7 + market-odds-anchor prompt + capped 0.10 longshot floor). Result: **mean Brier 0.0379** vs the previous Sonnet-4.6 baseline of 0.0639 — a **40.7% relative reduction** on this dataset.
- Snapshot of the Phase 1 (Sonnet) predictions saved at `data/predictions/multi_outcome_retrieval.phase1_sonnet.json`; new predictions at `data/predictions/multi_outcome_retrieval.json`; per-event diff at `reports/phase2_vs_phase1_backtest.json`.
- **Where the win came from (important honesty):** by outcome count, binary events (n=2, 14 of 26) drove almost the entire improvement: Brier 0.0879 → 0.0425 (Δ=−0.0454). All five of the top-5 wins are binary longshots where the old guard floored a confident-correct 0.05–0.10 prediction up to 0.25 (the floor bug). Opus 4.7's stronger anchoring is a secondary effect. Multi-outcome events were mixed — n=3 events improved (one big −0.0711), n=20 events regressed (+0.0459 average) because Opus puts more mass on outcome[0] than Sonnet did, which costs more when outcome[0] isn't the winner.
- **Don't overclaim:** 26 events is a tiny sample, and the dataset skews toward binary tennis matches where the longshot guard fix matters disproportionately. Live Prophet Arena events may have a different outcome-count distribution. Treat the 0.0379 number as "directionally validated, not converged."
- Decision: Rob asked for measured evidence; spend ~$2.60 for the rerun.
- Commit: this commit.

## 2026-05-16 · Gemini 3.1 Pro Preview ablation — keep Opus 4.7

- Ran `google/gemini-3.1-pro-preview` through OpenRouter using the SAME `predict_multi_outcome_retrieval` pipeline (same Brave retrieval, same anchor prompt, same longshot floor) on the 26-event sample-resolved set.
- Result: **mean Brier 0.4149 vs Opus 4.7's 0.0379** — Gemini was ~11x worse on this dataset, dominated by catastrophic multi-outcome failures (multi-mean 0.8115). Inspection showed Gemini emitting malformed JSON on multi-outcome events (trailing commas, bogus keys like `"1"` instead of outcome labels) — our prompt's "use EXACT outcome labels supplied" instruction didn't land.
- Total spend: ~$0.44.
- **Important lesson:** Gemini 3 Pro is the public Prophet Arena leaderboard's #1 fixed-context model. That ranking is on PA's own harness, not ours. **Leaderboard #1 ≠ best in your pipeline.** Worth quoting in any "model selection" portfolio section.
- Decision: stay on Opus 4.7 for production. The ablation script `scripts/ablate_openrouter.py` is now the harness for any future "should we swap?" question — run it on the same 26 events before any model swap.
- Decision: Rob authorized ablation spend instead of relying on intuition.
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

Decision: Rob authorized an aggressive ablation budget so production choices were based on measured data rather than undersampled intuition.

## 2026-05-17 · GPT-5.5 ablation — confirms the schema-compliance hypothesis

- Ran `openai/gpt-5.5` through the same `predict_multi_outcome_retrieval` pipeline on the 26-event sample-resolved set via `scripts/ablate_openrouter.py`. Spend ~$1.20.
- Result: mean Brier **0.3226** (8.5× worse than Opus 4.7's 0.0379).
- Decomposition: binary mean 0.0376 (*slightly better* than Opus 4.7's 0.0425); multi-outcome mean 0.6552 (37× worse than Opus 4.7's 0.0177).
- Decision: do not swap. Same JSON schema failure mode we've now observed in 4 of 4 non-Anthropic-Opus-4.7 models (GPT-5.2, GPT-5.5, Opus 4.6, Gemini 3.1 Pro). The schema-compliance pattern is robust across the OpenAI/Google/older-Anthropic axis.
- Note for the workshop paper: GPT-5.5's competitive binary number is interesting — for a binary-only pipeline, it would be a viable cheaper alternative to Opus 4.7. PA's event mix is unknown but Discord ("events won't be highly skewed") suggests both shapes will appear.
- Decision: newer OpenAI model checked after Rob asked whether a swap was warranted.
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

Decision: prompt-ablation discrepancy surfaced the bug. The original
metric mismatch is documented here; postmortem is the discipline.
- Commit: this commit + the backtest script fix.

---

## 2026-05-16 (late evening) — PA scoring formula confirmed; KEEP market-odds-anchoring

**Discovery via PA organizer Discord (Anri Gu + Jibang Wu, 2026-05-16 23:11 CT):**

> Total score = (team avg Brier − market avg Brier) × completion rate.
> Market Brier = calculated against snapshotted Kalshi/Polymarket
> prices at the time of prediction. Events close 2 days to 2 weeks out.

This is a **relative metric vs market**, not absolute Brier. The hard
test is `BSS > 0 vs market`, which markets are calibrated to defeat
by aggregating informed money.

**What this means for the SHIP-list from the parallel review agent:**

- **Item #1 (delete `_MULTI_OUTCOME_RETRIEVAL_SYSTEM_PROMPT` market-odds-anchoring block at forecast_track.py:1139-1147):**
  **DO NOT APPLY** without first measuring on a market-anchored
  baseline. The +0.0192 multi-only Brier improvement V1 vs V0 was
  measured against actual outcomes, not against market-Brier. Under
  PA's actual scoring rule, the anchoring block is protective:
  - When market is right and we anchor → we tie market Brier → score ≈ 0
  - When market is wrong and our model has signal → we beat market
  - Without anchoring, when market is right → we drift off-truth → lose
  Removing the anchor only helps if our model has signal the market
  systematically lacks across many event categories. n=26 unmeasured.
- **Item #2 (sum-to-1 dilution fix):** APPLY — multi-class scoring
  benefits, single-binary unaffected. Confirmed 7 of 26 events have
  prob sums &gt; 1.0 (max 2.35 on KXNHLCALDER-26).
- **Item #3 (silent retrieval degradation log):** APPLY — protects
  completion_rate by surfacing Brave fallback to operator.
- **Item #4 (exception boundary on /predict):** APPLY — directly
  protects completion_rate, the score multiplier. Highest priority.
- **Item #5 (missing-outcome fallback):** APPLY — uniform prior on
  small n is too aggressive; `min(prior, longshot_guard_floor(n))`
  shrinks unknowns toward 0 conservatively.

**Already-built infrastructure that becomes load-bearing:**

- `evaluation/brier.py:brier_skill_score(brier_model, brier_baseline)`
  — exact PA metric. Surfaced in summary.html this commit.
- `evaluation/brier.py:pnl_alpha_vs_market(p_final, p_market, outcome)`
  — per-event alpha vs market. Used once PA calls land with market
  prices in the request payload (or we infer from cited prices).
- `forecasting/market_blend.py` — Kalshi-paper-informed market-aware
  blending, offline-only. Not promoting now; might revisit if PA
  starts emitting explicit market prices in the request.

**FutureSim (Goel et al. 2026, OpenForecaster/futuresim):** Their
benchmark replays months chronologically on the OpenForesight
dataset; their best agent scores 23.6% accuracy / 0.054 Brier skill
score. Architecture not portable (multi-agent simulator vs our
webhook agent). Post-event: clone + run our agent through
OpenForesight as a workshop-paper extension.

Decision: Discord scoring-formula screenshot changed the interpretation.
The original "delete anchoring" recommendation came from a review pass
that did not have the scoring-rule context; its V0/V1 measurement was
valid, but the strategic implication reversed once the actual rule was
known.
Commit: this commit.

---

## 2026-05-16 (late evening) — Backtest leakage audit: 38.5% of events retrieve post-resolution content

**Audit (`scripts/check_retrieval_leakage.py`, this commit):**

Of 26 events in `sample-resolved`, **10 have at least one evidence URL
whose path contains word-boundaried "won", "wins", "winner", "champion",
"final", "results", or similar post-resolution markers** (38.5% of
events; 23.8% of all retrieved URLs are flagged). A first-pass
substring match without word boundaries overcounted to 24/26; the
honest number with `\b` boundaries is 10/26. Both numbers materially
support the same finding. Examples:

- KXTHEMASKEDSINGER-27JAN01 (resolved 2026-04-03):
  - <https://variety.com/2026/tv/news/the-masked-singer-season-14-finale-winner-ashlee-simpson-1236704931/>
  - URL was written AFTER Ashlee Simpson won; Brave indexed it because
    the event had resolved.
- KXNHLCALDER-26 (resolved 2026-05-14):
  - <https://www.espn.com/nhl/story/_/id/45381153/who-won-nhl-rookie-year-winners-year-list>
- KXTOURNAMENTOFCHAMPIONS-26DEC31 (resolved 2026-04-20):
  - <https://www.foodnetwork.com/shows/tournament-of-champions/articles/tournament-of-champion-vii-episodic-updates>

**What this means for our reported numbers:**

- Single-binary Brier 0.0378 on the 26-event resolved set is
  **best-case-with-hindsight**, not expected live performance. The
  agent has been retrieving the answer.
- The same leakage applies to every alternative-LLM ablation we ran on
  this dataset. **Cross-model rankings remain valid** (Opus 4.7 vs
  Opus 4.6 vs GPT-5.2 etc.) because all variants got the same
  hindsight benefit. Absolute Brier numbers are all inflated similarly.
- Live PA performance will be a different distribution: events arrive
  unresolved, so Brave Search returns only forecasting articles, base
  rates, and market quotes — not post-resolution recaps.
- This validates the FutureSim paper's methodology (Goel et al. 2026,
  arXiv:2605.15188): chronological replay is required to avoid this
  exact contamination. Our submission cannot retroactively fix it
  without a snapshotted pre-resolution news corpus.

**What we will report going forward:**

- Submission/REPORT.md and summary.html will gain an explicit
  "Backtest leakage" disclosure section. The 0.0378 number stays
  reported (it IS what `prophet forecast evaluate` says) but with
  a "hindsight upper bound" qualifier.
- Cross-model rankings remain the central finding because they are
  leakage-invariant.
- For live PA scoring, we expect higher absolute Brier than
  backtest suggests; the headline metric is BSS vs market, which is
  measured on the live event itself with no leakage opportunity.

Decision: Rob requested the E5 leakage audit; this records the result.
Commit: this commit (`scripts/check_retrieval_leakage.py` + the
findings written into `summary.html` + this entry).

---

## 2026-05-16 (very late) — E3 + E4 ablations: retrieval count + source priority

**E3 retrieval count sweep** (k = 3, 5, 8, 10) on the 26-event resolved set:

  k   binary    multi   multi-only
  3   0.06087   0.27783   0.40821
  5   0.03782   0.24357   0.42858
  8   0.03868   0.21627   0.36943
  10  0.05968   0.26365   0.37610

k=5 wins single-binary (matches OpenForecaster's plateau). k=8 wins
multi-class + multi-only Brier. k=10 regresses (noise dilution). k=3
is clearly worst. **Suggested production change**: adaptive retrieval
count = 5 for binary events, 8 for multi-outcome. Not auto-applying;
would need a small dispatcher in `forecast_track.predict_multi_outcome_retrieval`.

**E4 source-priority ablation** (3 modes through identical pipeline):

  mode             binary    multi
  broad           0.03936   0.25400
  official        0.03784   0.25256   (current production)
  exchanges_only  0.03657   0.23512

exchanges_only WINS both metrics: binary +3.4%, multi-class +7% over
production. Lead Brave results with kalshi.com/polymarket.com instead
of the current .gov/.edu-first. **Suggested production change**:
swap `_PRIORITY_DOMAINS` in forecast_track.py to lead with exchange
domains. Caveat: on the resolved sample-set most events have *zero*
exchange-domain hits in Brave results (only 2 of 5 Priority list
entries match anything). The win may be a small-n artifact.
**Recommendation**: try this on live PA traffic — exchange pages
likely surface more for unresolved events than for resolved ones.

**E1 abstain-to-market** (Haiku price-extraction from evidence): 0 of
26 events had cited market prices. Confirms our retrieval is news-heavy,
not exchange-heavy. Aligned with E4 finding: we should pull more from
exchanges directly.

**E2 verification prompt**: regressed Brier by +0.019. Combined with
self-critique replication regression (+0.003), two-of-two
failures for adversarial-review patterns over an already-calibrated
production model. **Negative result for the paper.**

**Spend total**: ~$25 in Anthropic + OpenRouter. Plenty of budget left.

Decision: Rob's "go run all experiments" instruction.
None auto-applied to production. PR review pending for the
exchanges_only switch and the adaptive-retrieval-count dispatcher.
Commit: this commit's batch.

---

## 2026-05-17 — Verification: E3 + E4 findings fail paired-bootstrap CI

Both production-candidate changes from the late-night ablation batch
failed the paired-bootstrap CI test on n=26 events. **Neither
applied to production.** Discipline win: the verification saved
us from chasing small-n noise.

**E4 (exchanges_only source priority) — paired bootstrap:**

  Scoring         | Mean Δ    | 95% CI                | Significant?
  ----------------|-----------|------------------------|--------------
  Single-binary   | +0.00128  | [-0.00167, +0.00550]  | NO (PA CLI)
  Multi-class     | +0.01744  | [+0.00133, +0.04016]  | YES (proper)

Per-event inspection: 22 of 26 events show **zero** change between
official and exchanges_only — the prioritization only affects events
where Brave returned an exchange URL. Of the 4 events with non-zero
delta, results split (WTA tennis match helps +0.05, ATP tennis match
hurts -0.01). On the metric PA actually scores (single-binary CLI),
the change is indistinguishable from noise.

**E3 (adaptive retrieval k=5 binary / k=8 multi) — paired bootstrap:**

  Comparison      | Mean Δ    | 95% CI                | Significant?
  ----------------|-----------|------------------------|--------------
  k=5 vs k=8 bin  | -0.00087  | [-0.00346, +0.00087]  | NO
  k=5 vs k=8 mc   | +0.02730  | [-0.02921, +0.10907]  | NO
  adaptive vs k=5 | -0.00087  | [-0.00346, +0.00087]  | NO

The k=5 vs k=8 binary delta is so small that even the bootstrap CI
on n=26 can't separate them. Multi-class shows a directional
preference for k=8 but the CI is wide enough that one or two events
swinging would flip the sign.

**What stays valid:**

- The headline Phase 2 delta (Sonnet 4.6 + old floor → Opus 4.7 +
  new floor) DID pass the bootstrap CI on the same dataset
  (CI [0.0143, 0.0374], excludes zero). That improvement was big
  enough (0.026 single-binary Brier) to clear small-n noise.
- For the workshop paper, E3 and E4 stay as "directional but
  inconclusive on n=26" not "production-improving wins."
- Production stays on current `_PRIORITY_DOMAINS` and `count=5` for
  retrieval — both unchanged.

**General methodology lesson:** On n=26, paired-bootstrap CIs at
α=0.05 need roughly |Δ| > 0.01 single-binary Brier to clear zero.
Anything smaller is indistinguishable from run-to-run LLM
stochasticity. Apply this bar to any future small-ablation
finding before shipping to production.

Decision: Rob's "verify before shipping" instruction.
The verification took ~10 minutes and zero spend. Saved a production
change that would have been ~0 EV on PA's actual metric. Commit:
this commit.

---

## 2026-05-17 (02:35 CT) — Intra-model variance: production = 0.0377 ± 0.0009

Ran `predict_multi_outcome_retrieval` 5 times sequentially on the
26-event resolved set. Same prompt, same retrieval, fresh LLM calls
(130 + 130 Brave fetches). Cost ~$13.

  run 1: 0.03820
  run 2: 0.03822
  run 3: 0.03822
  run 4: 0.03782
  run 5: 0.03613

  grand mean: 0.03772
  std:        0.00090  (CV = 2.4%)
  range:      [0.03613, 0.03822]
  canonical:  0.03782  (canonical file sits well inside ±1σ)

**Honest production number going forward: 0.0377 ± 0.0009.**

**Why this matters for ablation triage:**

The Phase 2 headline improvement was 0.026 Brier with bootstrap CI
[0.0143, 0.0374]. Intra-model noise (σ=0.001) is ~25× smaller than
the Phase 2 signal, which is why the bootstrap CI excludes zero.

For any future ablation: if the claimed Brier improvement is less
than 0.005 (roughly 5×σ), treat it as a noise candidate that needs
re-verification (paired bootstrap or multi-run averaging) before
claiming a production-worthy improvement.

This is the noise floor that retroactively justifies the rejections
of E3 (adaptive retrieval count, |Δ|=0.0009) and E4 (exchanges_only
source priority, |Δ|=0.0013) earlier today. Both fall inside the
intra-model noise band. Bootstrap CI was the correct gate.

Artifacts:
- `scripts/ablate_variance.py` (harness)
- `scripts/build_variance_plot.py` (3-panel interactive page)
- `data/predictions/variance_run_*.json` (raw)
- `data/predictions/variance_summary.json` (aggregates)
- `static/variance.html` (Plotly view, auth-gated like other research views)

Decision: Rob's "research budget worth spending on
variance and plots" instruction. Commit: `cb4a1a0` (variance) +
this commit (summary.html + DECISIONS entry).

---

## 2026-05-17 (11:30 CT) — Subset-1200 scale validation: 0.0378 was hindsight-inflated 3.2x

Ran the production `predict_multi_outcome_retrieval` variant on
Prophet Arena's public 1200-event resolved dataset
(`huggingface.co/datasets/prophetarena/Prophet-Arena-Subset-1200`).
Same prompt, same retrieval, same longshot floor, same Opus 4.7.
~$120 in API spend; ~16 minutes wall-clock.

**Result:**

  Backtest                 n      Brier     95% CI                  Leakage
  -----------------------  -----  --------  ----------------------  ----------------
  sample-resolved (orig)   26     0.0378    [0.0143, 0.0374] delta  38.5% / 23.8%
  Subset-1200 (scale)      1200   0.1224    [0.1102, 0.1351]         21.8% / 7.0%

  delta: +0.0846 (n=1200 is 3.2x worse than n=26)

**Honest reading:**

- The 0.0378 sample-resolved headline was inflated by hindsight: the
  26 events disproportionately mapped to well-indexed, post-resolution-
  rich web pages (38.5% events had a post-resolution URL marker).
- The Subset-1200 number 0.1224 is the more credible expected magnitude
  on live PA events. The sample is 46x larger, the leakage rate is
  roughly half, and it covers many more event types and tickers.
- Distribution at scale: median Brier 0.010, IQR [0.010, 0.144]. The
  pipeline produces near-perfect predictions on most events (typically
  0.9-floored picks on a clear favorite) and catastrophic ones on a
  long tail. The mean is dominated by the long tail.
- Parse-error rate at scale: 0.58% (7 of 1200 events). Pipeline scales
  cleanly; nothing breaks at 46x.

**What we did with this:**

- Added the Subset-1200 number to `static/summary.html` as a new top
  section "Scale-up validation on Subset-1200 (most credible number)".
  Both numbers are shown side-by-side; we do not replace the 0.0378
  number because it is what the local `prophet forecast evaluate` CLI
  computed against `data/resolved.json` + `data/actuals.json` and is
  reproducible.
- Updated `submission/PROJECT_STORY.md` "what we learned" #1 with the
  scale-up result and the honest framing.
- Did not retrofit the README or REPORT.md headline because those
  numbers are correctly scoped to the sample-resolved set. They cite
  the 26-event scope explicitly.

**Implication for the workshop paper:** the Subset-1200 run is the
right anchor for the absolute-Brier claim in any post-event writeup.
The cross-model rankings (Opus 4.7 vs 4.6 vs GPT-5.2 etc.) remain
valid on the 26-event set because they share retrieval and are
leakage-invariant in the same way; we did not re-run the 4 alternative
models on Subset-1200 (would cost ~$480 and the directional answer
already exists).

Artifacts:
- `scripts/ablate_subset_1200.py` (1200-event harness with HF loader)
- `scripts/analyze_subset_1200.py` (post-run analysis: Brier, CI,
  leakage audit, parse-error rate)
- `data/predictions/subset_1200.json` (raw predictions)
- `data/predictions/subset_1200_actuals.json` (binary actuals)
- `data/predictions/subset_1200_summary.json` (aggregate)

## 2026-05-17 — Prophet Arena confirmed endpoint mutability during scoring

Anri Gu from the Prophet Arena org (GitLab affiliation in handle) clarified
on Discord this afternoon, in response to Siddharth asking:

> Q: "For the forecasting track, can we still continue refining if we don't
>    change the endpoint during the evaluation phase?"
> A: "Since you're hosting it, you can update it as you'd like. We won't be
>    verifying that the code stays the same or anything."

**What this means for our submission:**

1. We can keep deploying improvements during the live evaluation window.
   The endpoint URL is stable; the implementation behind it is not pinned.
2. Combined with our paired-bootstrap CI promotion bar, this means we can
   measure new prompt/retrieval variants on the early PA calls and ship
   them mid-window if they clear the gate. The bar stays: |delta| > 0.01
   single-binary Brier with 95% CI excluding zero.
3. Risk: any change deployed mid-window mixes pre- and post-change
   scoring. We will tag commits with the deploy SHA and time so the
   post-event retrospective can split the live BSS series by deploy.

**How to apply:**

- Don't disable the promotion-gate discipline. The temptation is "PA is
  scoring, just push the change" — that's the same mistake as training-test
  contamination. Run the offline backtest first, get a CI, then deploy.
- Tag mid-window deploys explicitly in `docs/DECISIONS.md` so we can
  attribute live PA Brier deltas to specific commits.
- Document this in `submission/PROJECT_STORY.md` as "what's next" so
  judges see we know the latitude exists.

Source: Discord screenshot from 2026-05-17, conversation between Anri Gu
([GTLB]) and Siddharth at 1:26–1:28 PM local.

## 2026-05-17 17:30 CT — PR #12 partial 4+5 backtest regression; reverted before serving

Cherry-picked 2 of 3 prompt changes from closed PR #12 onto main as commit
`7662c9b5`:
- Both multi-outcome prompts: "Probabilities do NOT need to sum to 1" replaced
  with "Your probabilities for the outcomes should sum to approximately 1."
- `_predict_multi_outcome_retrieval_impl` missing-outcome fallback:
  `prior` (=1/n) replaced with `min(prior, longshot_guard_floor(n))` when n>2.

Held back the third change (anchor-block removal) as too risky without first
seeing PA's live distribution.

**What the measurement showed (paired-bootstrap, n=26, n_resamples=20000):**

| Metric | Pre-4+5 | With-4+5 | Delta | Notes |
| --- | --- | --- | --- | --- |
| Single-binary Brier (mean) | 0.0378 | 0.0445 | **+0.0066 (17.6% worse, relative)** | Point estimate |
| 95% paired-bootstrap CI on improvement | — | — | **[-0.0201, +0.0006]** | Crosses zero (barely) |
| Pr(improvement ≤ 0) | — | — | **0.87** | 87% probability of regression |

**Decision:** the change FAILS our published promotion gate
(|delta| > 0.01 AND 95% CI excludes zero). It clears neither condition:
|delta|=0.0066 < 0.01 threshold, and CI includes zero (upper bound +0.0006).

**Action taken:**
1. Reverted via `git revert 7662c9b5` → commit `13dc61f`.
2. Pushed and queued a redeploy of the safety-net-only state (`81b05ab6`,
   functionally identical to `1ed9bd63`).
3. Railway aborted the in-flight bad-prompts builds before any of them
   promoted to serving traffic. The bad code never served a /predict
   call.

**What we learned:**
- The PR #12 body claimed +0.0003 single-binary Brier (noise) when measuring
  4+5+anchor-removal *together*. That bundle may have a different interaction
  than 4+5 alone; the anchor removal might have been load-bearing for the
  +0.0003 number. Partial cherry-picks of bundled changes need their own CI.
- Run-to-run drift on Opus 4.7 is real (σ≈0.0009 per the variance run), but a
  +0.0066 delta is too large for run-noise alone. Some of this is a real
  effect from the sum-to-1 wording change.
- Confirms the discipline: every change runs through bootstrap CI before it
  ships, even when an earlier PR description claimed "low risk."

**Re-evaluation plan:**
After first PA call lands, look at outcome-count distribution. If multi-outcome
heavy, revisit 4+5+anchor-removal *together* (PR #12's full bundle) with a
fresh measurement against the live PA payload format.

Artifacts:
- `data/predictions/measurement_4_5/multi_outcome_retrieval.json` (gitignored;
  reproducible via `python scripts/backtest_forecast.py --variants multi_outcome_retrieval --out-dir data/predictions/measurement_4_5`)
- `data/predictions/measurement_4_5/backtest_summary.json` (same, gitignored)

## 2026-05-17 22:30 CT — Discord confirmations from Anri + Sravya during eval window

Three official rules surfaced after our submission. All are load-bearing.

### Rule 1: top-K events use marginal probabilities, server does not normalize

Quoted Discord question and Anri's reply:

> Q: "For multi-outcome events where multiple outcomes can be 'correct'
>    simultaneously (e.g., 'which 5 of these 26 will finish top 5'),
>    should per-outcome probabilities be:
>    (A) P(this is THE winner), summing to 1 across outcomes, or
>    (B) P(this is in the winning set), summing to K?"
> A (Anri): "We'll be doing B - we shouldn't be normalizing as the outcomes
>            are not always mutually exclusive."

**Implication for our code:** `forecast_track.py:apply_longshot_guard`
currently renormalizes when probability sum lands in [0.5, 1.5]. For
winner-take-all events that's correct; for top-K events that's a real
bug — it squashes marginal probabilities toward sum=1 and destroys the
intended scoring signal.

**Action:** add `_classify_event_semantics(event)` and a top-K-aware
guard branch. Winner-take-all path stays bit-identical to current
production. Top-K / multi-label / ordered-threshold paths floor but do
not renormalize.

**Promotion gate:** if the new branch only activates on detected non-
winner-take-all events, AND on winner-take-all the output bit-matches
current production, we promote before first top-K PA call lands. Waiting
for a top-K call to test would expose us to scoring on the bug.

### Rule 2: ~200 events over the 14-day eval window, daily cadence

Sravya, Discord:

> "~200 events for evaluation. Based on our experience, this should be
>  good enough to remove variance."

Per-day average ~14 events. Likely bursty (sports finals weekends, Fed
meetings, election days). Endpoint should accept burst traffic without
queueing.

### Rule 3: event close times range from 2 days to 2 weeks

Anri: "the close time of these events will range from within 2 days to
2 weeks." So events resolve continuously across the eval window, not all
at the end. That means leaderboard updates throughout the 14 days, not
just at close.

### Rule 4: cost guidance is ~$0.30/forecast

Sravya: "For reasonable agents, it's usually less than $0.3 per forecast
(even using most advanced models), so the cost should be manageable."

At 200 events × $0.30 = $60 max for the eval window. Our per-call cost
is ~$0.05 (Brave + Opus 4.7), so 200 events ≈ $10. Plenty of headroom.

### Rule 5: Anri-sanctioned abstain-to-market strategy

Anri: "One potential implementation is to only make prediction when you
are confident enough, otherwise you could just use the market probability
as your prediction."

**Implication for our code:** we already built and tested this as
variant `predict_abstain_to_market` (see 2026-05-17 E1 ablation entry
above). The bottleneck was Haiku price-extraction from Brave snippets
not reliably finding market prices. If PA's live payload includes a
snapshotted market-price field (the BSS scoring formula implies this),
abstain-to-market becomes trivial — just read the field. Inspect the
first PA payload to see whether market prices are present.

### Operating implications

1. **Endpoint mutability + measured-gate discipline continues.** The
   2026-05-17 17:30 CT 4+5 regression entry stays the precedent.
2. **Top-K classifier is now the highest-priority correctness fix.**
   Stage first, test deterministic equivalence on winner-take-all, then
   promote.
3. **Watch the first PA payload for a market-price field.** If present,
   wire abstain-to-market with a simple confidence threshold.
4. **No prompt or model changes.** Contract correctness only.

## 2026-05-18 00:40 CT — Top-K classifier + narrowed binary shortcut promoted to production

Deploy SHA: `a1f899b0` (cherry-picked from staging `4dcf1c1d` — classifier
commits only, no landing/audit/PDF moves).

UTC: 2026-05-18 05:40:28Z.

Commits promoted (in order):
- `24c6abd` docs(decisions): log 2026-05-17 22:30 CT Discord confirmations
- `1c6613e` feat(forecast): event-semantics classifier + top-K-aware guard
- `e0fee2d` fix(classifier): binary outcomes short-circuit to winner_take_all
- `a1f899b` fix(classifier): narrow binary shortcut to recognized mutex pairs only

**What changed in production behavior:**
- New: `_classify_event_semantics(event)` routes between `apply_longshot_guard`
  (winner_take_all, sum→1) and `apply_longshot_guard_topk` (top_k / multi_label
  / ordered_threshold, no renormalization).
- Recognized mutex pairs (Yes/No, True/False, Over/Under, Above/Below,
  Higher/Lower) short-circuit to winner_take_all even when title contains
  threshold language.
- Generic 2-outcome lists (Team A / Team B) fall through to the text classifier
  and pick up multi_label semantics from "qualify" / "nominated" / etc.

**Live smoke after deploy** (a1f899b0 @ 2026-05-18 05:40:28Z):
- Yes/No binary `"at least 2 cuts"` → sum 1.0000 (WTA) ✓
- Over/Under binary `"over/under 2.5%"` → sum 1.0000 (WTA) ✓
- Super Bowl 5-way WTA → sum 1.0000 ✓
- Top-5 of 12 NBA → sum 5.5800 (top_k marginals preserved) ✓
- Two-team qualify regression → sum 1.0700 (multi_label, not squashed) ✓

**Promotion-gate criteria met:**
- 300/300 tests pass (26 in tests/test_topk_classifier.py)
- Cherry-picked NOT ff-merged → no landing/docs noise on production
- Winner-take-all path bit-identical to pre-deploy production
- Reviewed twice after staging caught two real bugs in prior iterations

**Watcher:** PID 33661 alive, still polling, `last_run_at: null` at deploy time.

**Risk mitigation:** if PA's first batch shows a classifier mis-routing, the
fix is one revert commit on `forecast_track.py` reverting to pre-`a1f899b0`
behavior. Tagged `decisions_a1f899b0_2026-05-18T05:40:28Z` is the deploy
anchor in this log.

## 2026-05-19 · search-provider bake-off isolates leakage as the Brier inflator

First ablation that swaps the **retrieval source** instead of the LLM. Same
Opus 4.7, same prompt, same dedupe + longshot guard; only the search call
changes. Harness: `scripts/ablate_search_provider.py` (plugs into
`bootstrap_brier_ci.py` like every other variant). Run on the 26-event
sample-resolved set.

- **brave** (control, unfiltered): mean Brier **0.0377**, retrieval leakage 21.3%
  (23 / 108 evidence URLs carry post-resolution markers).
- **brave_fresh** (Brave `freshness` capped at each event's `close_time − 1d`):
  mean Brier **0.1179**, leakage **11.5%** (13 / 113 URLs).

Paired bootstrap CI (brave_fresh vs brave, n=26, 20k resamples): mean delta
**−0.0802**, 95% CI **[−0.136, −0.030]**, Pr(improvement ≤ 0) = **1.0000**.

**Finding:** removing post-resolution leakage from retrieval degrades backtest
Brier by **3.1×** (0.038 → 0.118), unambiguously (CI excludes zero). This is an
independent, mechanism-level confirmation of the 2026-05-17 Subset-1200 result
that "0.0378 was hindsight-inflated 3.2×" — same factor, different method. The
inflation lives in the retrieval, not the model.

**Implications:**
1. The honest out-of-sample estimate is the leakage-disciplined **~0.118**, not
   0.038. Every audience-facing surface that pins 0.0378 as the headline should
   lead with the date-disciplined number and label 0.038 as best-case-with-hindsight.
2. This does **not** require a production change. Live PA events are unresolved
   at query time, so post-resolution leakage is structurally impossible on live
   traffic — production already gets the "fresh" condition for free. The fix is
   to our **backtest methodology**: report `brave_fresh` as the primary number.
3. "Is Brave best?" was the wrong question — provider relevance wasn't the
   bottleneck; retrieval date-discipline was. Provider bake-off vs Tavily/Exa/
   Serper still pending (needs keys in `~/Desktop/variables.txt`), but the
   leakage axis matters more than the relevance axis for honest numbers.

Prediction files: `data/predictions/ablation_search_{brave,brave_fresh}.json`.
No production code touched.
