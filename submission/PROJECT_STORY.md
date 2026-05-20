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
   anchoring, and the corrected longshot floor scored a
   leakage-disciplined **0.118** single-binary Brier on the 26-event
   sample-resolved backtest. The Sonnet-to-Opus swap is the smaller
   part of the gain; evidence and post-processing discipline carry
   most of the result.

   The unfiltered backtest reported 0.038, and we could have led with
   that. We didn't, because we audited it. A 46x scale-up on PA's
   public 1200-event resolved dataset (`Prophet-Arena-Subset-1200` on
   HuggingFace) came in at **Brier 0.1224**, 95% bootstrap CI
   [0.110, 0.135] - three times the small-set number. So we built a
   retrieval ablation to find out why, and it confirmed the 0.038 was
   hindsight-inflated 3.1x by post-resolution leakage in the search
   results (see #2). We report **0.118** as the honest headline and
   keep 0.038 only as a best-case-with-hindsight bound. The two clean
   routes - date-disciplined `brave_fresh` (0.118) and Subset-1200
   (0.1224) - agree on the magnitude.

2. **The leakage audit is the result we are proudest of.** We built a
   search-provider / freshness ablation
   (`scripts/ablate_search_provider.py`) that holds the model, prompt,
   dedupe, and longshot guard constant and swaps only the retrieval
   source. The honest arm (`brave_fresh`) date-caps Brave to each
   event's close time so post-resolution sources cannot leak in.
   Result: Brier degrades from 0.038 to **0.118** (3.1x), retrieval
   leakage drops 21.3% -> 11.5%, and a paired bootstrap CI
   (n=26, 20K resamples) gives mean delta **-0.080**, 95% CI
   **[-0.136, -0.030]**, **Pr(improvement <= 0) = 1.0**. The inflation
   lives in the retrieval, not the model. Two channels, both bounded:
   *retrieval leakage* (capped by date-restricting search - and
   structurally impossible on live PA traffic, where events are
   unresolved at query time) and *model-parametric leakage* (bounded
   by Opus 4.7's ~Jan 2026 knowledge cutoff, so post-cutoff events are
   parametric-clean). `scripts/diagnostics.py` adds the
   confidence-conditional finding: >=0.8 confidence -> Brier ~0.02,
   but 0.5-0.7 -> ~0.26 (worse than a coin), overall ECE 0.226  - 
   which motivates abstaining to the market price near a 0.7
   confidence threshold.

3. **Public leaderboards don't predict pipeline performance.**
   Gemini 3.1 Pro tops the PA fixed-context board. In our pipeline
   with our prompt and our scoring rule, it was materially worse
   than Opus 4.7 under both reported metrics (single-binary Brier
   0.0983; multi-class Brier 0.4773), largely because it emitted
   probabilities for outcome keys that were not in the supplied list.

4. **Boundary cases are where calibration dies.** Two real bugs:
   - The longshot floor was `max(0.05, 0.5/n)`, which equals **0.25**
     for binary events. Every binary prediction was silently clamped
     into `[0.25, 0.75]`. New formula `min(0.10, max(0.05, 0.5/n))`
     caps at the Kalshi threshold. About 6x per-event Brier improvement
     on binary longshots.
   - An agreement gate had `abs(0.60 - 0.5) < 0.10` as an exclusion
     check; in IEEE-754 that's `0.09999999999999998` and excluded
     the exact-bucket case the spec explicitly admitted. Two-line
     fix, one boundary test.

5. **Schema compliance is part of the result.** The weakest
   alternatives failed around multi-outcome JSON: keys that didn't
   match outcome labels, malformed nesting, and smart-quote
   contamination. We shipped a 5-stage parser, fuzzy outcome-label
   matching, and an outcomes safety-net (binary heuristic + Haiku
   fallback). The verify gate is now loud after silently swallowing
   pytest failures for a full session, fixed by `a46a0e6`.

6. **Server-authoritative rules.** `risk.py` imports
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
  ablations at 50% discount (`scripts/batch_ablate.py`); a
  leakage-free evaluation firehose - `scripts/generate_sports_slate.py`
  (keyless ESPN pregame slates), `scripts/auto_resolve_sports.py` +
  `scripts/auto_resolve_finance.py` (keyless auto-resolution),
  `scripts/diagnostics.py` (stratified Brier + reliability + ECE), and
  `scripts/ablate_search_provider.py` (retrieval bake-off)
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

# Reproduce the headline (leakage-disciplined 0.118) backtest:
python scripts/ablate_search_provider.py --providers brave,brave_fresh
python scripts/diagnostics.py \
  --pred data/predictions/ablation_search_brave_fresh.json \
  --label brave_fresh

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
- **Grow the leakage-free firehose.** The retrieval leakage in the
  26-event set is exactly what the freshness ablation measured (3.1x
  inflation). The shadow-event pipeline (keyless ESPN pregame slates +
  auto-resolution) now generates clean, forecast-before-resolve events
  daily; the next step is widening it beyond the sports-heavy mix
  (forward shadow set so far: n=11, mean winner Brier 0.256) so the
  honest number rests on a larger, category-balanced sample.
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
