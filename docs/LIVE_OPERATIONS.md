# Live Operations

Current handoff for the Prophet Hacks forecasting endpoint.

## Production Snapshot

| Item | Value |
| --- | --- |
| Team | `CanadaHacks` |
| Project | `The Oracles` |
| Track | Forecasting |
| Host | Railway project `mindful-unity`, service `oracles-agent`, environment `production` |
| Predict endpoint | `POST https://agent.forecastingpath.com/predict` |
| Health URL | `https://agent.forecastingpath.com/healthz` |
| Dashboard | `https://agent.forecastingpath.com/dashboard` |
| FastAPI docs | `https://agent.forecastingpath.com/docs` |
| Production variant | `multi_outcome_retrieval` |

As of 2026-05-16 13:20 CT, Prophet Arena reports the endpoint registered and
active, with no calls yet from the platform and no open forecast events.

## What The Endpoint Does

1. Accepts an event JSON payload from Prophet Arena at `/predict`.
2. Routes the event to the selected `forecast_track.py` variant.
3. For `multi_outcome_retrieval`, builds one Brave Search query from the event
   title plus the most informative outcome label.
4. Dedupes evidence by domain and prefers `.gov`, `.edu`, exchanges of record,
   and major news sources.
5. Calls Claude Sonnet 4.6 for per-outcome probabilities.
6. Applies the Kalshi longshot guard: each outcome is floored at
   `max(0.05, 0.5 / n_outcomes)`, then the distribution is renormalized when
   feasible.
7. Returns Prophet Arena's required response shape:

```json
{
  "probabilities": [
    {"market": "<outcome label>", "probability": 0.42}
  ],
  "rationale": "..."
}
```

The dashboard keeps only in-memory recent predictions. A Railway restart clears
the dashboard history but does not affect Prophet Arena's scored records.

## Deploy

Use the existing Railway-linked repo context:

```bash
RAILWAY_CALLER="skill:use-railway@1.2.1" \
RAILWAY_AGENT_SESSION="railway-skill-$(date +%s)" \
railway up --service oracles-agent --environment production --detach \
  -m "<short deploy summary>"
```

Poll the deployment:

```bash
RAILWAY_CALLER="skill:use-railway@1.2.1" \
RAILWAY_AGENT_SESSION="<same-session-id>" \
railway deployment list --service oracles-agent --limit 3 --json
```

Verify production after `SUCCESS`:

```bash
curl -fsS https://agent.forecastingpath.com/healthz
curl -fsS https://agent.forecastingpath.com/predictions
```

For a visual check, open the dashboard at desktop and mobile widths and confirm:

- no browser console errors
- no horizontal overflow at 390px width
- the status banner explains `Waiting for first call` while `last_run_at` is null
- KaTeX renders the Brier and Kalshi formulas

## Runtime Checks

Railway:

```bash
RAILWAY_CALLER="skill:use-railway@1.2.1" \
RAILWAY_AGENT_SESSION="railway-skill-$(date +%s)" \
railway status --json

RAILWAY_CALLER="skill:use-railway@1.2.1" \
RAILWAY_AGENT_SESSION="<same-session-id>" \
railway logs --service oracles-agent --lines 120 --json
```

Prophet Arena state, without printing secrets:

```bash
.venv/bin/python - <<'PY'
import os
from dotenv import load_dotenv
import httpx

load_dotenv(".env")
headers = {"X-API-Key": os.environ.get("PA_SERVER_API_KEY", "")}
for name, url in {
    "endpoint": "https://api.aiprophet.dev/forecast/endpoints/CanadaHacks",
    "scores": "https://api.aiprophet.dev/forecast/scores",
    "open_events": "https://api.aiprophet.dev/forecast/events?status=open",
}.items():
    r = httpx.get(url, headers=headers, timeout=10)
    print(name, r.status_code)
    data = r.json()
    if isinstance(data, dict):
        print({k: data.get(k) for k in (
            "endpoint_url",
            "is_active",
            "last_run_at",
            "last_run_status",
            "last_run_n_predictions",
            "team_name",
        ) if k in data})
    elif isinstance(data, list):
        print("list_len", len(data))
PY
```

## Common Incidents

### Dashboard says active but no predictions

This is expected while Prophet Arena has not posted open events or has not
called the endpoint. Check:

```bash
curl -fsS https://agent.forecastingpath.com/predictions
```

`{"count": 0, "predictions": []}` means the process is healthy but has not
served a prediction since its last start.

### Retrieval silently falls back

`multi_outcome_retrieval` falls back to `multi_outcome` when
`BRAVE_SEARCH_API_KEY` is missing or Brave fails. Check Railway variables and
runtime logs for:

```text
BRAVE_SEARCH_API_KEY missing; ... falling back to predict_multi_outcome
```

Do not print secret values in logs or chat.

### Railway deploy succeeds but public endpoint is stale

Check the latest deployment ID and status:

```bash
railway deployment list --service oracles-agent --limit 3 --json
```

Then hit `/healthz`. The `variant` field should be `multi_outcome_retrieval`.

### Need rollback

Prefer a new deploy from a known-good commit over force-pushing or deleting
deployments. Current known-good production commit after the dashboard mobile
fix is `64ac36c`.
