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
