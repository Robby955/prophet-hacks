# prophet-hacks

Prophet Hacks 2026 forecasting agent for Team `CanadaHacks`, project **The Oracles**.

Live at <https://forecastingpath.com/>. Predicts per-outcome probabilities for
Prophet Arena events, scored by Brier (lower is better).

## What this is

An evidence-grounded forecasting agent. For each event Prophet Arena hands us:

1. Build a Brave Search query from title + most-informative outcome.
2. Top 5 web results, deduped by domain (.gov / .edu / official sources first).
3. **Claude Opus 4.7** reads title + rules + evidence snippets through a system
   prompt that explicitly instructs anchoring to any cited market odds.
4. **Kalshi-paper longshot guard**: per-outcome probability floored at
   `min(0.10, max(0.05, 0.5 / n_outcomes))`. The `0.10` cap is the empirical
   Kalshi threshold; sub-$0.10 contracts lose >60% on average.
5. Return `{"probabilities": [{"market", "probability"}, ...]}` to PA.
6. Full pipeline trace (Brave query, raw model output, per-stage latency,
   fuzzy-match decisions, warnings) stored per call for `/predictions` audit.

The hacky-prose words are not load-bearing. The detail above is exactly what
runs in production at commit `e8c1beb9` (and whatever's newer at `/healthz.commit`).

## Production

| Item | Value |
| --- | --- |
| Public landing | <https://forecastingpath.com/> |
| Live dashboard | <https://agent.forecastingpath.com/dashboard> (PIN-gated via `/login`) |
| Predict endpoint | `POST https://agent.forecastingpath.com/predict` (public, Prophet Arena calls this) |
| Health + commit SHA | <https://agent.forecastingpath.com/healthz> |
| Host | Railway project `mindful-unity`, service `oracles-agent` |
| Production variant | `multi_outcome_retrieval` (Opus 4.7 + Brave + anchor prompt + 0.10 floor) |

`/dashboard`, `/compare`, `/compare-open` redirect to `/login` for browsers,
return JSON 401 for API callers. `/predict` and `/healthz` stay public.

## Results — 26-event sample-resolved backtest

Same pipeline (Brave + anchor prompt + 0.10 floor), swap the LLM:

| Model | Mean Brier | Binary (n=14) | Multi (n=12) |
| --- | --- | --- | --- |
| **Claude Opus 4.7** (production) | **0.0379** | 0.0425 | 0.0177 |
| Claude Sonnet 4.6 (previous prod) | 0.0639 | 0.0879 | — |
| Claude Opus 4.6 | 0.2264 | 0.0438 | 0.4396 |
| OpenAI GPT-5.2 | 0.2584 | 0.0538 | 0.4971 |
| Gemini 3.1 Pro Preview | 0.4149 | 0.0750 | 0.8115 |
| _random 0.5 baseline_ | 0.250 | — | — |
| _uniform 1/n prior_ | 0.219 | — | — |

Production beat the previous Sonnet baseline by **40.7% relative** on this set.

### Honest decomposition of the win

- The Sonnet→Opus 4.7 swap is the smaller half of the gain.
- The bigger half is fixing a **silent production bug** in
  `longshot_guard_floor`: old formula `max(0.05, 0.5/n)` returned 0.25 for
  binary events, silently clamping every binary prediction into
  `[0.25, 0.75]`. New formula caps at the Kalshi-paper threshold of 0.10.
  ~6× Brier improvement on binary longshots alone.
- Multi-outcome events were _mixed_ post-swap: Opus 4.7 is more confident
  than Sonnet, which helps when right (n=3 events) and hurts more when
  wrong (n=20). Net positive on this set but not on every event.

### Why we kept Opus 4.7 over leaderboard-ranked alternatives

Gemini 3.1 Pro Preview is the public Prophet Arena fixed-context leaderboard's
#1. In our pipeline with our prompt and our scoring rule, it placed last —
catastrophic multi-outcome JSON schema failures (emitting trailing commas,
bogus keys, or probability mass on labels not in the outcome list).
**The Opus 4.7 win on this dataset is dominated by schema compliance, not
raw reasoning.** See `docs/DECISIONS.md` for the per-model autopsy.

## Engineering process

- **Verify gate** (`./scripts/agent/verify.sh`) — pytest + smoke import +
  dry-run. Used to silently swallow failures; now loud. **~200 tests**
  passing as of last verify.
- **Preflight gate** (`scripts/preflight.sh`) — runs before any deploy:
  verify green, working tree clean, HEAD = origin/main, upload-size
  sanity (caught a real 18MB worktree bloat bug), prints live vs local
  SHA delta.
- **Deploy wrapper** (`scripts/agent/deploy.sh`) — single safe path to
  `railway up`. Pins commit SHA into `PROPHET_BUILD_COMMIT_SHA` env so
  `/healthz.commit` reflects what's actually serving.
- **Pipeline trace** — every `/predict` call captures Brave query,
  raw LLM output, parse-path, per-stage latency (ms), fuzzy-match
  decisions, warnings. Visible on `/predictions` (auth required), NOT
  sent back to PA.
- **Decisions log** (`docs/DECISIONS.md`) — append-only, 12+ dated entries
  including every bug postmortem.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.11+ supported; 3.13 is the dev baseline.

## Environment

Copy `.env.example` to `.env` and fill in. `.env` is gitignored.

| Var | Required for | Notes |
| --- | --- | --- |
| `PA_SERVER_API_KEY` | always | Prophet Arena API key, sent as `X-API-Key` |
| `PA_SERVER_URL` | no | Defaults to `https://api.aiprophet.dev` |
| `ANTHROPIC_API_KEY` | production | Opus 4.7 lives here |
| `BRAVE_SEARCH_API_KEY` | production | Web evidence retrieval |
| `OPENAI_API_KEY` | OpenAI variants + Haiku fallback | |
| `OPENROUTER_API_KEY` | ablations | `scripts/ablate_openrouter.py` |
| `PROPHET_AGENT_VARIANT` | production | Set to `multi_outcome_retrieval` |
| `DASHBOARD_AUTH_TOKEN` | production | Auth cookie value after PIN entry |
| `DASHBOARD_PIN` | production | Numeric PIN for `/login` |
| `PROPHET_BUILD_COMMIT_SHA` | production | Set by `scripts/agent/deploy.sh` |

Production secret store: `~/Desktop/variables.txt` (outside the repo).

## Run locally

```bash
source .venv/bin/activate
PROPHET_AGENT_VARIANT=multi_outcome_retrieval \
  uvicorn forecast_agent_server:app --host 127.0.0.1 --port 8000
```

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok","variant":"multi_outcome_retrieval","commit":"...",...}

curl -X POST http://127.0.0.1:8000/predict \
  -H 'content-type: application/json' \
  -d '{
    "event_ticker": "TEST",
    "market_ticker": "TEST",
    "title": "Will the US Federal Reserve cut rates at the December 2026 meeting?",
    "category": "Economics",
    "close_time": "2026-12-31T23:59:59Z",
    "outcomes": ["Yes", "No"]
  }'
```

Expected response:

```json
{
  "probabilities": [
    {"market": "Yes", "probability": 0.55},
    {"market": "No",  "probability": 0.45}
  ],
  "rationale": "..."
}
```

## Backtests + ablations

The 26-event sample-resolved set is in `data/resolved.json` (pulled with
`prophet forecast retrieve --dataset sample-resolved --include-resolved`).

```bash
# Standard backtest (one or more variants):
python scripts/backtest_forecast.py \
  --events data/resolved.json \
  --actuals data/actuals.json \
  --variants multi_outcome_retrieval

# Swap-the-LLM ablation through OpenRouter:
python scripts/ablate_openrouter.py \
  --model google/gemini-3.1-pro-preview \
  --workers 4

# Post-event scoring (Brier / BSS / ECE / Murphy decomposition):
python scripts/analyze_results.py \
  --predictions-url=https://agent.forecastingpath.com/predictions \
  --token="$DASHBOARD_AUTH_TOKEN" \
  --actuals=data/actuals.json
```

Per-variant predictions land in `data/predictions/`. The dashboard
`/compare` route renders all of them in a 5-model × 26-event grid with
Brier color-coding.

## Forecast variants

Defined in `forecast_track.py`, served via `forecast_agent_server.py`'s
`PROPHET_AGENT_VARIANT` env switch.

| Variant | Description |
| --- | --- |
| **`multi_outcome_retrieval`** | **Production.** Brave → 5 chunks → Opus 4.7 + anchor prompt → 0.10 floor. |
| `multi_outcome` | One Sonnet 4.6 multi-outcome call, no retrieval. Kalshi guard applied. |
| `multi_outcome_sc3` | k=3 parallel `multi_outcome` calls, averaged per outcome. |
| `single_llm` | One Sonnet 4.6 call, legacy binary `p_yes`. Server distributes across outcomes. |
| `opus_47`, `opus_46` | One Opus call (no retrieval). Underperformed standalone. |
| `gpt55`, `gpt52` | One OpenAI call. Cross-vendor sanity check. |
| `ensemble_logit` | Sonnet + GPT-5.5 logit-mean blend. |
| `ensemble_leaderboard` | Three-way logit-mean of Sonnet + Opus 4.6 + GPT-5.2. |
| `sonnet_cot`, `sonnet_cot_shrink` | Structured chain-of-thought experiments. |
| `hybrid_routed` | GPT for binary, multi-outcome prompt otherwise. |
| `uniform_prior` | `1/n_outcomes`. Free control baseline. |

## Key files

| Path | What |
| --- | --- |
| `forecast_track.py` | All `predict_*` variants. `predict_multi_outcome_retrieval` is production. |
| `forecast_agent_server.py` | FastAPI app: `/predict`, `/dashboard`, `/compare`, `/compare-open`, `/login`, `/healthz`, `/predictions`, `/events` |
| `risk.py` | Hard caps. Imports `ai_prophet_core.ruleset` and asserts at import time. |
| `forecasting/` | Composable forecasting modules (Kalshi guards, SAE shrinkage, market blend, reliability tracking). Not all wired into production yet. |
| `evaluation/` | Proper scoring rules (Brier, BSS, ECE, Murphy decomposition, no-leakage check). |
| `scripts/preflight.sh` | Pre-deploy gate. |
| `scripts/agent/deploy.sh` | Safe deploy wrapper (preflight + commit SHA pin + `railway up`). |
| `scripts/ablate_openrouter.py` | Swap-the-LLM ablation harness for any OpenRouter-hosted model. |
| `scripts/analyze_results.py` | Post-event scoring (Brier, BSS, ECE, Murphy decomposition). |

## Docs

- **`docs/DECISIONS.md`** — append-only decision log. Read this to understand _why_ anything is the way it is. 12+ dated entries including every bug postmortem.
- **`docs/HANDOFF.md`** — single-page state for picking up cold.
- **`docs/AGENT_STATUS.md`** — multi-agent coordination + active task ownership.
- **`docs/LIVE_OPERATIONS.md`** — production deploy / triage handoff.
- **`docs/RUNBOOK.md`** — incident response patterns.
- **`docs/STATUS.yaml`** — machine-readable status snapshot.
- **`agent_protocol.md`** — coding-agent rules. Required reading before changes.

## Verify before merging

```bash
PATH="$PWD/.venv/bin:$PATH" ./scripts/agent/verify.sh
```

PATH prefix matters in shells where `python` isn't globally installed.
Gate runs: pytest, smoke import, `agent.py --dry-run`. Failures are
loud — no silent skips.

## Trading-track skeleton (not the live submission path)

The original trading-track scaffolding (`agent.py`, `forecaster.py`, JSONL
traces, risk caps) remains in the repo because it's useful for risk and
observability work. It's **not** what's served at `agent.forecastingpath.com`.
Per [Jibang Wu's Discord clarification 2026-05-16](https://prophetarena.co/developer)
teams cannot enter both tracks; we chose forecasting.

```bash
python agent.py --slug smoke --dry-run        # no API calls
python agent.py --slug <slug> --once           # one tick and exit
```

JSONL traces under `trace/<slug>/<tick_id>.jsonl`. `risk.py` is authoritative
for hard caps and asserts at import time.

## License + credit

Built by Rob Sneiderman for Prophet Hacks 2026.
Multi-agent collaboration: Claude (this session) + Codex worked in parallel,
coordinated via `docs/AGENT_STATUS.md`.
