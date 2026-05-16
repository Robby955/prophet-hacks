# Agent protocol

Owner: Rob Sneiderman (`robbysneiderman@gmail.com`).

This file is the compact handoff for coding agents working on the live
Prophet Hacks repo. Treat `README.md`, `docs/LIVE_OPERATIONS.md`, and
`docs/STATUS.yaml` as the source of truth when this file gets stale.

## Current Production Path

- Host: Railway project `mindful-unity`, service `oracles-agent`,
  environment `production`.
- Public forecast endpoint: `POST https://agent.forecastingpath.com/predict`.
- Public health: `GET https://agent.forecastingpath.com/healthz`.
- Public root: `GET https://agent.forecastingpath.com/`.
- Private monitor: `/dashboard`, `/predictions`, and `/events` require
  `DASHBOARD_AUTH_TOKEN` in production.
- Production variant: `PROPHET_AGENT_VARIANT=multi_outcome_retrieval`.
- Root domain: `forecastingpath.com` is a Railway custom domain; DNS and TLS
  state should be checked in Railway/Cloudflare before assuming it is live.
- Vercel is not in the current production path.

Prophet Arena calls `/predict` directly. Do not put auth, redirects, forms,
or HTML in front of that route.

## Actual Repo Shape

Core live forecast path:

```text
forecast_agent_server.py   FastAPI service served by Railway
forecast_track.py          forecasting-track variants
railway.toml               Railway start and healthcheck config
README.md                  current production and local run docs
docs/LIVE_OPERATIONS.md    deploy, verification, and incident handoff
docs/STATUS.yaml           machine-readable current state
tests/                     regression tests
```

Trading-track skeleton retained for risk and trace discipline:

```text
agent.py
forecaster.py
market_filter.py
risk.py
logger.py
config.yaml
```

Experimental/offline modules may exist under `forecasting/` and `evaluation/`.
Keep them out of the live path until they have tests, documented assumptions,
and measured improvement over the active baseline.

## Verify Before Shipping

Use the repo gate:

```bash
PATH="$PWD/.venv/bin:$PATH" ./scripts/agent/verify.sh
```

The gate currently runs:

1. configured typecheck when available
2. `pytest tests/`
3. smoke import of the core trading modules
4. `python agent.py --slug verify-smoke --dry-run`

If runtime service code changes, verify production after deploy:

```bash
curl -fsS https://agent.forecastingpath.com/healthz
curl -sS -o /tmp/dashboard.out -w '%{http_code}' \
  https://agent.forecastingpath.com/dashboard
curl -fsS -H "x-dashboard-token: $DASHBOARD_AUTH_TOKEN" \
  https://agent.forecastingpath.com/predictions
```

Expected production behavior:

- `/healthz` returns 200 JSON.
- unauthenticated `/dashboard` returns 401.
- authenticated `/predictions` returns JSON.
- `/predict` returns 405 on GET and remains POST-only/public.

## Deploy

Use the existing Railway service:

```bash
RAILWAY_CALLER="skill:use-railway@1.2.1" \
RAILWAY_AGENT_SESSION="railway-skill-$(date +%s)" \
railway up --service oracles-agent --environment production --detach \
  -m "<short deploy summary>"
```

Poll:

```bash
RAILWAY_CALLER="skill:use-railway@1.2.1" \
RAILWAY_AGENT_SESSION="<same-session-id>" \
railway deployment list --service oracles-agent --limit 5 --json
```

## Model And Evaluation Rules

- Production currently serves `multi_outcome_retrieval`.
- `multi_outcome_retrieval` uses Brave Search evidence and the configured
  Anthropic forecast model in `forecast_track.py`; if retrieval fails it falls
  back to `multi_outcome`.
- GPT and ensemble variants are experimental unless explicitly promoted in
  `docs/DECISIONS.md`.
- Brier is lower-is-better. Accept a variant over market-only only when
  `Brier <= market_baseline` or `BSS-vs-market > 0` on the relevant holdout.
- Track ECE separately from Brier. Sharp but miscalibrated forecasts are not
  automatically improvements.
- Do not claim benchmark wins without the command, dataset, sample size, and
  leakage caveat.

## Hard Stops

- Do not commit secrets or print secret values in logs.
- Do not change the `/predict` response contract without a dedicated test and
  Prophet Arena compatibility check.
- Do not make live monitoring public during the event.
- Do not add unpinned dependencies.
- Do not use missing or invented model names.
- Do not treat offline retrieval on resolved events as a clean live estimate;
  web search can leak post-resolution information.
- Do not let a model choose trade size. Risk and sizing stay in code.

## Coordination

- Update `docs/AGENT_STATUS.md` when taking ownership of active files.
- Do not edit `agent.py` or `config.yaml` without checking the status board.
- Use new commits only. Do not amend pushed commits.
- Local git email must stay `robbysneiderman@gmail.com`.
