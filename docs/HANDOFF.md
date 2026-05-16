# Handoff — single-page state for picking up cold

Use this when another agent (or future-you) needs to take over without
context. Last updated 2026-05-16 14:51 CT (during Prophet Hacks 2026,
Saturday afternoon).

If the hackathon is over: jump to `docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md`.

---

## What this repo is

A live forecasting agent submitted to **Prophet Hacks 2026** by team
**CanadaHacks**, project name **The Oracles**. We submit to the
**forecasting track**, scored by Brier (lower is better). Prophet Arena
calls our endpoint with each event; we return per-outcome probabilities.

The repo doubles as a career portfolio piece — `docs/DECISIONS.md`, the
JSONL traces, this handoff, and the post-event retrospective are
intentional artifacts, not afterthoughts.

## Live production right now

| Thing | URL / value | Notes |
|---|---|---|
| Endpoint hostname (primary) | `https://agent.forecastingpath.com` | Railway-hosted |
| Apex (same backend) | `https://forecastingpath.com` | redirects to /dashboard |
| Health | `/healthz` | public, 200 |
| Predict | `POST /predict` | public (Prophet Arena hits this) |
| Dashboard | `/dashboard?token=$DASHBOARD_AUTH_TOKEN` | auth-protected |
| Predictions history | `GET /predictions` (same auth) | in-memory list |
| Active variant | `multi_outcome_retrieval` | Brave search → Opus 4.7 → longshot floor |
| Forecast model | `claude-opus-4-7` | Phase 2 swap from Sonnet 4.6 |
| Triage model | `gpt-5.4-mini` (not currently used live) | reserved |
| Search | Brave Search (BRAVE_SEARCH_API_KEY) | 5 chunks, deduped |
| Hosted on | Railway service `oracles-agent` in `mindful-unity` project | manual `railway up` deploys |
| Endpoint registration | Prophet Arena team `CanadaHacks`, active | `last_run_at: None` (no PA call yet at handoff time) |

Dashboard auth token lives at `/tmp/forecastpath-dashboard-url` on
Rob's machine. **Never paste it into chat or commit it.**

## Strategy in one paragraph

For every event Prophet Arena sends:

1. Build a Brave search query from event title + most informative outcome.
2. Hit Brave for 5 results, dedupe by source priority (.gov/.edu first).
3. Inject evidence titles+snippets into Opus 4.7 with a strict system
   prompt that includes a **market-odds anchoring** block — if the
   evidence cites implied probabilities, anchor to them.
4. Apply the Kalshi longshot floor: per-outcome probability ≥
   `min(0.10, max(0.05, 0.5/n_outcomes))`. The 0.10 ceiling on the
   floor was added 2026-05-16 to fix a bug where binary events were
   silently clamped to [0.25, 0.75].
5. Return `{"probabilities": [{"market": "<outcome>", "probability":
   <0..1>}, ...]}`. Probabilities don't need to sum to 1; PA normalizes.

## What's been deployed (commit history)

```
62f08a3  fix(dashboard): reflect Phase 2 (Opus 4.7, anchoring, 0.10 floor cap)
9652016  feat(forecast): Opus 4.7 + market-odds-anchor prompt + fix binary floor bug   <- Phase 2
a254665  fix(eval): harden review-found edge cases                                     <- Codex
b5ed6de  feat(eval): cherry-pick observability + Kalshi market_blend from v3-v6        <- Phase 1
cafd6f6  chore(ops): track agent guide and predictions watcher                         <- Phase 1
2e38088  fix(dashboard): protect live monitor                                          <- Codex earlier
```

`main` is what Railway serves (after a manual `railway up`).
GitHub auto-deploy is NOT wired — pushing to main does NOT trigger a
Railway build. Production updates require `railway up` from a logged-in
shell or the Railway MCP tool.

## Open work / Phase 3 candidates (priority order)

1. **PR #1 cherry-pick** — `calibrator.py` + `ensemble.py` for a
   median-of-logits + disagreement-penalty ensemble. Build
   `predict_multi_outcome_retrieval_ensemble` (Opus 4.7 + GPT-5.2 + same
   Brave evidence). Estimated +0.005–0.020 BSS at 2x cost. PR is
   conflicting; cherry-pick the two modules and skip the rest.

2. **PR #4 SAE shrinkage** — `forecasting/borrowed_strength.py` is the
   real upside (estimated +0.025–0.040 BSS from the agent review).
   Hierarchical Bayesian shrinkage in logit space. Wire into
   `predict_multi_outcome_retrieval` after the first live PA call so
   we have real event shapes to test against.

3. **Triage gate** — port the idea from `proposed_retrieval/base.py`
   (skip Brave when triage is confident, e.g. max prob > 0.7).
   Cost optimization, not a Brier improvement. Worth doing if event
   volume turns out high.

4. **Reliability diagram on the dashboard** — `evaluation/ece.py`
   already returns the bin data. Add a simple SVG calibration curve to
   `forecast_agent_server.py` once we have ≥30 resolved predictions.

5. **OPEN PRs to deal with** — #1 (v2 calibrated ensemble),
   #4 (v3-v6 SAE stack). #3 already closed. Both will sit forever
   conflicting; either selectively cherry-pick or close.

## Files you should know

| File | What |
|---|---|
| `forecast_track.py` | All `predict_*` variants. `predict_multi_outcome_retrieval` is the production path. |
| `forecast_agent_server.py` | FastAPI server: `/predict`, `/dashboard`, `/predictions`, `/events`, `/healthz`. |
| `forecaster.py` | Original trading-track forecast logic (binary `p_yes`). Not on the production critical path; legacy. |
| `risk.py` | Hard caps. Imports `ai_prophet_core.ruleset` and asserts at import time. |
| `forecasting/market_blend.py` | Kalshi guards (binary, need `p_market` we don't currently have). Available for future use. |
| `evaluation/{brier,ece,returns,no_leakage_check}.py` | Proper scoring rules. Used by `scripts/analyze_results.py`. |
| `scripts/analyze_results.py` | Post-event analysis CLI. Reads predictions + actuals, emits Brier/BSS/ECE/Murphy. |
| `scripts/watch_predictions.sh` | Polls PA every 60s, mac-notifies on first call / events opening / scores posting. |
| `agent_protocol.md` | Coding-agent rules (locked default system, cost caps, hard NOs). Read before changes. |
| `config.yaml` | Model defaults; `risk.py` constants win on conflict. |
| `docs/AGENT_STATUS.md` | Who's working on what right now. Claim files here before editing. |
| `docs/DECISIONS.md` | Locked strategic choices and why. |
| `docs/LIVE_OPERATIONS.md` | Restart procedures and operational state. |

## Commits + git rules

- **No AI co-author trailers ever** (no `Co-Authored-By: Claude`, no
  `🤖 Generated`). This repo flips public after the event; commit history
  should read as Rob's work.
- Convention: `<scope>(<area>): <one-line>`.
- Never `git commit --amend` a pushed commit; new commits only.
- `git config --local user.email` must equal `robbysneiderman@gmail.com`.

## What to do when a new PA call lands

Watcher (PID logged at `/tmp/oracles_watch.log`) mac-notifies on three
transitions: `last_run_at` flips, open events appear, or scores post.
When the notification fires:

1. Read `/predictions` (auth-protected) to see what we sent back.
2. Check Railway logs for `oracles-agent` for any errors during the call.
3. Verify the prediction shape: outcomes match event.outcomes, each
   probability in [0.01, 0.99], no NaN, longshot floor applied.
4. If Brave retrieval failed, the rationale will say so and
   `evidence_urls` will be empty — fall-through path, still safe.

## Costs and budget

- Single live call: ~$0.10 (Opus 4.7 on ~5500 input + ~500 output) + Brave (free tier).
- Sub-$10 spends are pre-authorized; don't ask Rob about them.
- Spend above $50 in a single session → flag in `docs/AGENT_STATUS.md`.

## Things that will trip you up

1. **`gpt-5.5-mini` does NOT exist.** OpenAI mini tier tops out at
   `gpt-5.4-mini`. Common LLM trap.
2. **Don't add a Kalshi API key.** `PA_SERVER_API_KEY` + `ANTHROPIC_API_KEY`
   are the only keys we use.
3. **`/predict` is public; `/dashboard`+`/predictions`+`/events` need
   `DASHBOARD_AUTH_TOKEN`.** Don't accidentally lock down /predict.
4. **Submission schema is `{"probabilities": [{"market","probability"}]}`,
   NOT binary `{"p_yes"}`.** Easy trap.
5. **Railway auto-deploy is NOT wired.** Pushing to main does nothing.
   Run `railway up --service oracles-agent --detach` to actually deploy.
   Verify with `railway deployment list --service oracles-agent`.
6. **`apply_longshot_guard` floors AFTER the LLM call.** If you change
   the floor logic, you may invalidate previously-stored predictions'
   semantics. Document any change in `docs/DECISIONS.md`.

---

End of handoff. If anything here is wrong or out of date, fix it in the
same change that makes it wrong.
