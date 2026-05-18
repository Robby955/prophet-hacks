# The Oracles - Prophet Hacks 2026 forecasting submission

*Team: **CanadaHacks** - Project: **The Oracles** - Track: Forecasting - Author: Rob Sneiderman ([@Robby955](https://github.com/Robby955))*

---

## Inspiration

Prediction-market forecasting is a calibration problem first and a
model-selection problem second. Prophet Arena scores with Brier, which
punishes confident-and-wrong forecasts more than uncertain-and-wrong
forecasts, so the central task is matching conviction to evidence.

That shaped The Oracles from the first commit. The stance was simple:
prefer a calibrated, monitored, source-aware, market-aware endpoint
over an active system that cannot explain its errors. The project is
built around measured probability estimates, traceability, and a clear
rule for rejecting changes that do not survive evaluation.

## What we built

**The Oracles** lives at
[`agent.forecastingpath.com/predict`](https://agent.forecastingpath.com/predict).
Prophet Arena posts one event at a time; we respond with per-outcome
probabilities backed by recent web evidence and anchored to any cited
market odds.

**Five stages, every one logged on the live dashboard:**

1. **Query.** Brave query from title + most informative outcome label.
2. **Retrieve.** Brave Search `count=5`; graceful fallback if the
   key is missing or the call fails.
3. **Rank.** Dedupe by domain, prioritize `.gov`/`.edu`/exchanges,
   cap at 5 chunks.
4. **Forecast.** Claude Opus 4.7. System prompt enforces a 0.50 to 0.90
   calibration scale and market-odds anchoring. LLMs systematically
   overweight vivid narratives; the prompt resists that.
5. **Floor.** Kalshi longshot guard: every probability floored at
   `min(0.10, max(0.05, 0.5/n))`. Grounded in the Kalshi finding that
   buyers of <$0.10 contracts lose >60%.

The full per-prediction trace lives at the PIN-protected `/dashboard`.

## What we learned

1. **Retrieval was the bigger win than model choice.** Same prompt
   without Brave scored Brier 0.19. Adding retrieval, market-odds
   anchoring, and the corrected longshot floor scored **0.0378** on
   the 26-event sample-resolved backtest. The Sonnet-to-Opus swap is
   the smaller part of the measured Phase 2 gain; evidence and
   post-processing discipline carry most of the result.

   We also ran a 46x scale-up validation on PA's public 1200-event
   resolved dataset (`Prophet-Arena-Subset-1200` on HuggingFace).
   That headline came in at **Brier 0.1224** with 95% bootstrap CI
   [0.110, 0.135]. The 0.0378 number is hindsight-rich on a small,
   well-indexed slice; the 0.1224 is the more credible expected
   magnitude on the live distribution. We report both because they
   are real on different samples; the Subset-1200 number is the one
   we expect to roughly match live PA performance.

2. **Public leaderboards don't predict pipeline performance.**
   Gemini 3.1 Pro tops the PA fixed-context board. In our pipeline
   with our prompt and our scoring rule, it was materially worse
   than Opus 4.7 under both reported metrics (single-binary Brier
   0.0983; multi-class Brier 0.4773), largely because it emitted
   probabilities for outcome keys that were not in the supplied list.

3. **Boundary cases are where calibration dies.** Two real bugs:
   - The longshot floor was `max(0.05, 0.5/n)`, which equals **0.25**
     for binary events. Every binary prediction was silently clamped
     into `[0.25, 0.75]`. New formula `min(0.10, max(0.05, 0.5/n))`
     caps at the Kalshi threshold. About 6x per-event Brier improvement
     on binary longshots.
   - An agreement gate had `abs(0.60 - 0.5) < 0.10` as an exclusion
     check; in IEEE-754 that's `0.09999999999999998` and excluded
     the exact-bucket case the spec explicitly admitted. Two-line
     fix, one boundary test.

4. **Schema compliance is part of the result.** The weakest
   alternatives failed around multi-outcome JSON: keys that didn't
   match outcome labels, malformed nesting, and smart-quote
   contamination. We shipped a 5-stage parser, fuzzy outcome-label
   matching, and an outcomes safety-net (binary heuristic + Haiku
   fallback). The verify gate is now loud after silently swallowing
   pytest failures for a full session, fixed by `a46a0e6`.

5. **Server-authoritative rules.** `risk.py` imports
   `ai_prophet_core.ruleset` and asserts at import time that our caps
   <= server caps. Any future drift fails on `import risk`, not at
   first rejected request.

## How we built it

```
event JSON in
   │
   ▼
Build query  ── Brave Search ── Dedupe/rank ── Opus 4.7 ── Kalshi floor
                                                  │
                                                  ▼
                              JSON probabilities + per-call trace
```

**Stack.** Python 3.13, FastAPI + uvicorn on Railway (Nixpacks build).
Anthropic Opus 4.7 for the forecast call (with Sonnet 4.6 + Haiku 4.5
in the fallback chain), Brave Search for retrieval, OpenRouter for
the multi-vendor ablation harness. All deploys go through
`scripts/agent/deploy.sh` which runs a preflight gate (verify +
working-tree-clean + upload-size sanity + HEAD-pushed check) before
`railway up`. The live commit SHA is pinned via
`PROPHET_BUILD_COMMIT_SHA` and surfaced on `/healthz` so anyone can
verify what code is serving with a single curl.

**Engineering discipline as a feature.** The decisions log
(`docs/DECISIONS.md`) has 18+ dated entries: every bug postmortem,
every model-swap rationale, every "we tested this and rejected it"
finding. The full-check script (`scripts/full_check.sh`) runs a
11-step audit across source state, deployed surface, auth gates, and
watcher process, used as the "is everything OK?" one-stop check
before sleeping or before the event window opens.

**Parallel workstreams.** Implementation, evaluation, dashboard, and
ops work were coordinated with explicit file-ownership claims. That kept
parser hardening, offline research variants, dashboard work, and deploy
tooling from colliding.

## Challenges

- **Deploy bloat.** Three Railway deploys failed silently with TLS
  errors before local worktree state was accidentally included in the
  Railway upload. Fix: ignore the worktrees plus preflight `du -sh`
  check. Real engineering failure, real fix.
- **A model that beat us on the leaderboard placed last in our
  pipeline.** See "what we learned" #2 above.
- **A longshot floor formula that looked right but was wrong.** See
  "what we learned" #3.
- **Verifying a deploy actually landed.** Pushing to main does NOT
  auto-deploy on Railway. `/healthz` now returns the build commit
  SHA so we can curl and confirm.

## Built with

- **Languages**: Python 3.13
- **Server**: FastAPI, uvicorn, Pydantic v2
- **Hosting**: Railway (Nixpacks)
- **Forecast LLM**: Anthropic Claude Opus 4.7 (with Sonnet 4.6 +
  Haiku 4.5 fallbacks)
- **Retrieval**: Brave Search Web API
- **Ablation harness**: OpenRouter unified API (Gemini, GPT-5.2,
  Opus 4.6, Sonnet 4.6 tested through same pipeline)
- **Frontend**: hand-written HTML + KaTeX + SSE for live updates
- **Testing**: pytest, 260+ tests
- **CI/CD**: bash scripts (`scripts/preflight.sh`,
  `scripts/agent/deploy.sh`, `scripts/full_check.sh`)
- **Tooling**: Anthropic Message Batches API harness for offline
  ablations at 50% discount (`scripts/batch_ablate.py`)
- **Coordination**: explicit file-ownership notes, `docs/DECISIONS.md`

## Reproducibility

```bash
git clone git@github.com:Robby955/prophet-hacks.git && cd prophet-hacks
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in ANTHROPIC_API_KEY + BRAVE_SEARCH_API_KEY

# Run the live endpoint locally:
PROPHET_AGENT_VARIANT=multi_outcome_retrieval \
  uvicorn forecast_agent_server:app --host 127.0.0.1 --port 8000

# Reproduce the headline backtest number:
python scripts/backtest_forecast.py \
  --events data/resolved.json \
  --actuals data/actuals.json \
  --variants multi_outcome_retrieval

# End-to-end audit against your local instance:
./scripts/full_check.sh --host http://localhost:8000
```

## What's next

PA's organizer (Anri) confirmed on Discord that we can keep deploying
during the scoring window. That changes the next-steps list from
"things to do after the event" to "things to ship as they clear the
promotion gate":

- **Mid-window experiments under the same gate.** Any new prompt or
  retrieval variant has to clear |delta| > 0.01 single-binary Brier
  with 95% CI excluding zero on the offline backtest before it deploys.
  Mid-window deploys get tagged in `docs/DECISIONS.md` with SHA + UTC
  timestamp so the post-event retrospective can attribute live Brier
  deltas to specific changes.
- **Run on a non-leaking benchmark.** The 26-event resolved set has
  retrieval leakage (38.5% post-resolution URLs). Re-run on FutureSim's
  OpenForesight chronological-replay benchmark.
- **Wire abstain-to-market in production.** When PA's live payload
  arrives with snapshotted Polymarket/Kalshi prices, use the
  `pnl_alpha_vs_market` metric in `evaluation/brier.py` to gate when
  the model trades vs. defers to the market price.
- **Confidence-aware critique.** The two adversarial-review patterns
  we tried regressed; an open hypothesis is to only revise low-
  confidence initial predictions, leaving confident-and-correct ones
  alone.

Live: <https://forecastingpath.com> - Repo:
<https://github.com/Robby955/prophet-hacks>
