# CanadaHacks — Prophet Hacks 2026 forecasting submission

*Team: **CanadaHacks** · Project: **The Oracles** · Track: Forecasting · Author: Rob Sneiderman ([@Robby955](https://github.com/Robby955))*

---

## Inspiration

Forecasting prediction markets is a statistics problem dressed up as a
software problem. The flashiest agents lose to the most calibrated
ones — the ones that know not just *what* they predict but *how much*
they should believe it. Prophet Arena scores on Brier, which punishes
confident-and-wrong twice as hard as hedging-and-wrong, so the whole
game is matching conviction to evidence.

My ICML 2026 work on equivalence testing taught the same lesson in a
different language: the hardest part is never the model, it's the
discipline of asking "is this difference real, or am I fooling
myself?" That intuition shaped The Oracles from the first commit. Our
locked competitive stance, set before kickoff in our `v7 competition
landscape addendum`, was simple: **don't be the flashiest agent, be
the most calibrated, monitored, source-aware, market-aware one.** A
boring system that always knows why it acted beats a dramatic one
that can't diagnose its errors.

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
4. **Forecast.** Claude Opus 4.7. System prompt enforces a 0.50–0.90
   calibration scale AND market-odds anchoring — LLMs systematically
   overweight vivid narratives; the prompt resists that.
5. **Floor.** Kalshi longshot guard: every probability floored at
   `min(0.10, max(0.05, 0.5/n))`. Grounded in the Kalshi finding that
   buyers of <$0.10 contracts lose >60%.

The full per-prediction trace lives at the PIN-protected `/dashboard`.

## What we learned

1. **Retrieval was the bigger win than model choice.** Same prompt
   without Brave → Brier 0.19. Add retrieval + market-odds anchoring
   → **0.0379** on the 26-event sample-resolved backtest. The
   Sonnet→Opus swap alone is worth maybe 0.02; the rest is evidence.

2. **Public leaderboards don't predict pipeline performance.**
   Gemini 3.1 Pro tops the PA fixed-context board. In our pipeline
   with our prompt and our scoring rule, it placed last (Brier
   **0.4149**) — almost entirely because it emitted multi-outcome
   JSON for outcome keys that weren't in the supplied list. Worth
   quoting any time someone proposes a model swap based on a public
   ranking.

3. **Boundary cases are where calibration dies.** Two real bugs:
   - The longshot floor was `max(0.05, 0.5/n)`, which equals **0.25**
     for binary events. Every binary prediction was silently clamped
     into `[0.25, 0.75]`. New formula `min(0.10, max(0.05, 0.5/n))`
     caps at the Kalshi threshold. ~6× per-event Brier improvement
     on binary longshots.
   - An agreement gate had `abs(0.60 - 0.5) < 0.10` as an exclusion
     check; in IEEE-754 that's `0.09999999999999998` and excluded
     the exact-bucket case the spec explicitly admitted. Two-line
     fix, one boundary test.

4. **Schema compliance is half the win.** Three of four alternative
   models in our ablation broke on multi-outcome JSON — keys that
   didn't match outcome labels, malformed nesting, smart-quote
   contamination. We shipped a 5-stage parser, fuzzy outcome-label
   matching, and an outcomes safety-net (binary heuristic + Haiku
   fallback). The verify gate is now *loud* after silently swallowing
   pytest failures for a full session — fixed by `a46a0e6`.

5. **Server-authoritative rules.** `risk.py` imports
   `ai_prophet_core.ruleset` and asserts at import time that our caps
   ≤ server caps. Any future drift fails on `import risk`, not at
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
(`docs/DECISIONS.md`) has 13+ dated entries — every bug postmortem,
every model-swap rationale, every "we tested this and rejected it"
finding. The full-check script (`scripts/full_check.sh`) runs a
10-step audit across source state, deployed surface, auth gates, and
watcher process — used as the "is everything OK?" one-stop check
before sleeping or before the event window opens.

**Multi-agent collaboration.** Two coding agents (Claude + Codex)
worked in parallel through the sprint, coordinated via
`docs/AGENT_STATUS.md` with explicit file-ownership claims. Zero
merge conflicts; complementary work (parser hardening + SAE variant
on one side, dashboard + docs + ops tooling on the other).

## Challenges

- **Deploy bloat.** Three Railway deploys failed silently with TLS
  errors before we noticed `.claude/worktrees/` was uploading 18MB
  of agent state to Railway. Fix: `.gitignore` the worktrees +
  preflight `du -sh` check. Real engineering failure, real fix.
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
- **Testing**: pytest, ~200 tests
- **CI/CD**: bash scripts (`scripts/preflight.sh`,
  `scripts/agent/deploy.sh`, `scripts/full_check.sh`)
- **Tooling**: Anthropic Message Batches API harness for offline
  ablations at 50% discount (`scripts/batch_ablate.py`)
- **Coordination**: `docs/AGENT_STATUS.md`, `docs/DECISIONS.md`

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

Live: <https://forecastingpath.com> · Repo:
<https://github.com/Robby955/prophet-hacks>
