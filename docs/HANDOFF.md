# Handoff: single-page state for picking up cold

Use this when another agent (or future-you) needs to take over without
context. Last updated 2026-05-17 (Prophet Hacks 2026, hackathon submission
ready, eval window pending; live health verified at commit `7c3f04e9`).

If the hackathon is over: run `./scripts/post_event_orchestrator.sh
--actuals <path>` for the auto-generated retrospective draft, then
jump to `docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md`.

## Quick reads in priority order

1. `submission/REPORT.md`: the single-page submission summary
2. `docs/FINDINGS.md`: the research-grade write-up of what was measured
3. `docs/WORKSHOP_PAPER_DRAFT.md`: 4-page workshop-paper draft
4. `docs/ADVERSARIAL_REVIEW.md`: strongest self-critique and claim limits
5. `docs/QUANT_PORTFOLIO_ARTIFACTS.md`: what survives even if live score disappoints
6. `docs/DEMO_CAPTURE_GUIDE.md`: screenshots and short demo capture flow
7. `docs/DECISIONS.md`: append-only decision log (every bug + every model swap)
8. `docs/RUNBOOK.md`: operational triage including the first-PA-call playbook
9. `docs/SUBMISSION.md`: how to fill the hackathon submission form
10. `docs/AGENT_STATUS.md`: multi-agent coordination, current Codex handoff list

---

## What this repo is

A live forecasting agent submitted to **Prophet Hacks 2026** by team
**CanadaHacks**, project name **The Oracles**. We submit to the
**forecasting track**, scored by Brier (lower is better). Prophet Arena
calls our endpoint with each event; we return per-outcome probabilities.

The repo doubles as a career portfolio piece: `docs/DECISIONS.md`, the
JSONL traces, this handoff, and the post-event retrospective are
intentional artifacts, not afterthoughts.

## Live production right now

| Thing | URL / value | Notes |
|---|---|---|
| Endpoint hostname (primary) | `https://agent.forecastingpath.com` | Railway-hosted |
| Apex (same backend) | `https://forecastingpath.com` | public landing page |
| Health | `/healthz` | public, 200 |
| Predict | `POST /predict` | public (Prophet Arena hits this) |
| Dashboard | `/dashboard` | auth-protected; redirects to `/login` |
| Predictions history | `GET /predictions` (same auth) | in-memory list |
| Active variant | `multi_outcome_retrieval` | Brave search → Opus 4.7 → longshot floor |
| Forecast model | `claude-opus-4-7` | Phase 2 swap from Sonnet 4.6 |
| Triage model | `gpt-5.4-mini` (not currently used live) | reserved |
| Search | Brave Search (BRAVE_SEARCH_API_KEY) | 5 chunks, deduped |
| Hosted on | Railway service `oracles-agent` in `mindful-unity` project | manual `railway up` deploys |
| Endpoint registration | Prophet Arena team `CanadaHacks`, active | endpoint API now requires auth from this shell |
| Live commit | `7c3f04e9` | verified via both `/healthz` hosts on 2026-05-17 |

Dashboard auth uses a PIN-backed login cookie in production. Tokens and
PIN files live outside the repo. **Never paste them into chat or commit
them.**

## Strategy in one paragraph

For every event Prophet Arena sends:

1. Build a Brave search query from event title + most informative outcome.
2. Hit Brave for 5 results, dedupe by source priority (.gov/.edu first).
3. Inject evidence titles+snippets into Opus 4.7 with a strict system
   prompt that includes a **market-odds anchoring** block. If the
   evidence cites implied probabilities, anchor to them.
4. Apply the Kalshi longshot floor: per-outcome probability ≥
   `min(0.10, max(0.05, 0.5/n_outcomes))`. The 0.10 ceiling on the
   floor was added 2026-05-16 to fix a bug where binary events were
   silently clamped to [0.25, 0.75].
5. Return `{"probabilities": [{"market": "<outcome>", "probability":
   <0..1>}, ...]}`. Probabilities don't need to sum to 1; PA normalizes.

## What's been deployed (commit history)

```
7c3f04e  fix(overnight): resolve tour json and scatter labels
0799caa  docs(review): adversarial self-review + fix README 40.7/40.8 inconsistency
8b65079  feat(server): shepherd tour + observatory polish + dashboard tour wiring
a2a6880  feat(overnight): regenerated pages from C1-C5 + variance ablation
0e7cf4b  docs(variance): surface 0.0377 +- 0.0009 in summary + DECISIONS
cb4a1a0  feat(variance): 5-run intra-model variance ablation + interactive plot
f59022b  fix(submission-polish): README diagram + gallery credit footer + login spacing
12959ea  feat(landing+login): surface public report; clarify operator-only gating
9652016  feat(forecast): Opus 4.7 + market-odds-anchor prompt + fix binary floor bug
```

`main` is what Railway serves (after a manual `railway up`).
GitHub auto-deploy is NOT wired. Pushing to main does NOT trigger a
Railway build. Production updates require `railway up` from a logged-in
shell or the Railway MCP tool.

## Open work / Phase 3 candidates (priority order)

1. **First live PA call review:** inspect `/predictions`, Railway logs,
   payload shape, outcome matching, parse path, warnings, and latency.

2. **Post-event actuals flow:** when actuals are available, run
   `./scripts/post_event_orchestrator.sh --actuals <path>` and fill
   `docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md`.

3. **Open PR #12 review:** the only open PR from `gh pr list` on
   2026-05-17. It touches forecast quality and server behavior, so do
   not merge without measured improvement on the right metric and Rob's
   explicit approval.

4. **Public release cleanup:** use
   `docs/QUANT_PORTFOLIO_ARTIFACTS.md` before making the repo public:
   redact secrets, preserve demo captures, preserve negative ablations,
   and avoid unsupported market-beating claims.

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

- **No agent co-author trailers ever** (no `Co-Authored-By: Claude`, no
  generated-with-agent trailers). This repo flips public after the event; commit history
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
   `evidence_urls` will be empty: fall-through path, still safe.

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
