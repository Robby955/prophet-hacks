# prophet-hacks

Prophet Hacks 2026 repo for Team `CanadaHacks`, project `The Oracles`.

The live submission path is the Prophet Arena forecasting track. Prophet Arena
calls our HTTP endpoint, receives one probability per listed outcome, and scores
the result with Brier score after events resolve. The repo also keeps the
original trading-track skeleton because its risk caps, JSONL traces, and
runbook are useful portfolio artifacts.

## Production

| Item | Value |
| --- | --- |
| Root site | <https://forecastingpath.com/> once DNS propagation finishes |
| Dashboard | <https://agent.forecastingpath.com/dashboard> (token-protected in production) |
| Predict endpoint | `POST https://agent.forecastingpath.com/predict` |
| Health | <https://agent.forecastingpath.com/healthz> |
| Host | Railway project `mindful-unity`, service `oracles-agent`, environment `production` |
| Team | `CanadaHacks` |
| Production variant | `multi_outcome_retrieval` |

The production service is configured by `railway.toml` and starts with:

```bash
/opt/venv/bin/uvicorn forecast_agent_server:app --host 0.0.0.0 --port $PORT
```

The FastAPI root route is a public status page. The live dashboard,
machine-readable prediction history, and SSE stream are token-protected in
production; `/predict` remains public because Prophet Arena calls it directly.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.11+ is supported. Python 3.13 is the current dev baseline.

## Environment

Copy `.env.example` to `.env` and fill in the keys. `.env` is gitignored.

| Var | Required | Notes |
| --- | --- | --- |
| `PA_SERVER_API_KEY` | yes | Prophet Arena API key, sent as `X-API-Key` |
| `PA_SERVER_URL` | no | Defaults to `https://api.aiprophet.dev` |
| `OPENAI_API_KEY` | for OpenAI variants | Used by GPT variants and ensembles |
| `ANTHROPIC_API_KEY` | for Anthropic variants | Used by Sonnet and Opus variants |
| `BRAVE_SEARCH_API_KEY` | for `multi_outcome_retrieval` | Used for the production retrieval variant |
| `PROPHET_AGENT_VARIANT` | no | FastAPI variant. Production uses `multi_outcome_retrieval` |
| `DASHBOARD_AUTH_TOKEN` | production monitoring | Protects `/dashboard`, `/predictions`, and `/events`; leave unset for local dev |
| `PROPHET_FORECAST_TRACK_MODEL` | no | Forecast-track Anthropic model. Default: `claude-sonnet-4-6` |
| `PROPHET_FORECAST_OPENAI_MODEL` | no | Forecast-track OpenAI model. Default: `gpt-5.5` |
| `PROPHET_FORECAST_MODEL` | no | Trading-track forecast model override |
| `PROPHET_TRIAGE_MODEL` | no | Trading-track triage model override. Default: `openai/gpt-5.4-mini` |
| `PA_N_TICKS` | no | Trading-track experiment-length hint. Default: `96` |

Secrets also live outside the repo in `~/Desktop/variables.txt`; use
`scripts/sync_env_from_variables.sh` when useful.

## Run The Forecast Endpoint Locally

```bash
source .venv/bin/activate
PROPHET_AGENT_VARIANT=multi_outcome_retrieval \
  uvicorn forecast_agent_server:app --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/healthz
```

Example prediction request:

```bash
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

Expected response shape:

```json
{
  "probabilities": [
    {"market": "Yes", "probability": 0.55},
    {"market": "No", "probability": 0.45}
  ],
  "rationale": "..."
}
```

## Forecasting Backtests

```bash
source .venv/bin/activate
prophet forecast retrieve --dataset sample-resolved --include-resolved -o data/resolved.json
python scripts/build_actuals.py data/resolved.json data/actuals.json
python scripts/backtest_forecast.py \
  --events data/resolved.json \
  --actuals data/actuals.json \
  --variants uniform_prior,single_llm,multi_outcome,multi_outcome_retrieval
```

Reference results from the 26-event `sample-resolved` run are tracked in
`data/predictions/backtest_summary.json` and surfaced on the live dashboard.
The retrieval backtest is optimistic because Brave can find articles about
already-resolved sample events; live future events do not have that leakage.

## Forecast Variants

Defined in `forecast_track.py` and served through `forecast_agent_server.py`:

- `uniform_prior` - deterministic `1 / len(outcomes)`, no model call.
- `single_llm` - one Claude Sonnet 4.6 call, legacy binary `p_yes`.
- `opus_47`, `opus_46` - one Opus call, legacy binary `p_yes`.
- `gpt55`, `gpt52` - one OpenAI call, legacy binary `p_yes`.
- `ensemble_logit`, `ensemble_leaderboard` - logit-space model blends.
- `sonnet_cot`, `sonnet_cot_shrink` - structured prompt experiments.
- `multi_outcome` - one Sonnet call that returns per-outcome probabilities.
- `multi_outcome_sc3` - three parallel `multi_outcome` calls averaged by outcome.
- `multi_outcome_retrieval` - Brave Search plus Sonnet per-outcome forecast.
- `hybrid_routed` - GPT for binary events, multi-outcome prompt otherwise.

## Trading Skeleton

The trading agent is not the live hackathon submission path, but it remains
useful for risk and observability work:

```bash
python agent.py --slug smoke --dry-run
python agent.py --slug <slug> --variant model-forecast-no-retrieval
python agent.py --slug <slug> --once
```

One JSONL file is written per tick under `trace/<slug>/<tick_id>.jsonl`.
`risk.py` is authoritative for hard caps and asserts at import time that local
caps are at least as strict as `ai_prophet_core.ruleset`.

## Verify

Use the project gate before merging or deploying:

```bash
PATH="$PWD/.venv/bin:$PATH" ./scripts/agent/verify.sh
```

The PATH prefix matters in shells where `python` is not globally installed.
The gate runs the available tests, smoke imports, and `agent.py --dry-run`.

## Key Docs

- `docs/LIVE_OPERATIONS.md` - current production endpoint, deploy, and triage handoff.
- `docs/STATUS.yaml` - machine-readable live status artifact for future agents.
- `docs/RUNBOOK.md` - incident patterns and recovery checks.
- `docs/DECISIONS.md` - append-only decision log.
- `SUBMISSION_NOTES.md` - trace schema and submission gates.
