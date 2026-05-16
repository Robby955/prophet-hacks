"""FastAPI agent server for Prophet Hacks forecasting track.

The Prophet Arena server pulls predictions from a registered HTTP endpoint
when new events appear. We wrap a `predict_*` variant from
`forecast_track.py` behind a /predict route and expose it publicly via a
cloudflared tunnel.

Usage:
    # 1. Start the server:
    .venv/bin/python forecast_agent_server.py
    # listens on 0.0.0.0:8000

    # 2. In a SEPARATE terminal, start the tunnel:
    cloudflared tunnel --url http://localhost:8000
    # prints a public URL like https://<random>.trycloudflare.com

    # 3. Register the endpoint with Prophet Arena (one-shot):
    .venv/bin/prophet forecast register \\
        --team-name CanadaHacks \\
        --endpoint-url https://<random>.trycloudflare.com/predict

Health checks:
    curl http://localhost:8000/healthz       -> {"status":"ok","variant":"..."}
    curl -X POST http://localhost:8000/predict -H 'content-type: application/json' \\
         -d '{"event_ticker":"TEST","market_ticker":"TEST","title":"Will p=0.5?","category":"Test","close_time":"2099-01-01T00:00:00Z","outcomes":["Yes","No"]}'

Variant routing:
    Set PROPHET_AGENT_VARIANT env var to swap which predict_* in
    forecast_track.py gets called. Defaults to single_llm. Set to
    `multi_outcome` to emit real per-outcome probabilities directly from
    the model.

Response schema (per the 2026-05-16 server docs):
    {"probabilities": [{"market": "<outcome>", "probability": <0..1>}, ...]}

For legacy single-`p_yes` variants the server distributes p_yes across
outcomes (outcomes[0] gets p_yes, the rest evenly share 1-p_yes). For the
`multi_outcome` variant the per-outcome probabilities are taken straight
from the model.
"""
from __future__ import annotations

import collections
import json
import logging
import os
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

load_dotenv()

import forecast_track  # noqa: E402


# In-memory ring buffer of the last N predictions served. Used by /dashboard.
_PREDICTION_HISTORY: collections.deque = collections.deque(maxlen=50)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("oracles.agent")


# Which variant to serve from /predict. Defaults to the Brier-winning one
# from the sample-resolved backtest.
_VARIANT_NAME = os.environ.get("PROPHET_AGENT_VARIANT", "single_llm")
_VARIANT_FN = {
    "uniform_prior": forecast_track.predict_uniform_prior,
    "single_llm": forecast_track.predict_single_llm,
    "opus_47": forecast_track.predict_opus_47,
    "opus_46": forecast_track.predict_opus_46,
    "gpt55": forecast_track.predict_gpt55,
    "gpt52": forecast_track.predict_gpt52,
    "ensemble_logit": forecast_track.predict_ensemble_logit,
    "ensemble_leaderboard": forecast_track.predict_ensemble_leaderboard,
    "sonnet_cot": forecast_track.predict_sonnet_cot,
    "sonnet_cot_shrink": forecast_track.predict_sonnet_cot_shrink,
    "multi_outcome": forecast_track.predict_multi_outcome,
    "multi_outcome_sc3": forecast_track.predict_multi_outcome_sc3,
    "multi_outcome_retrieval": forecast_track.predict_multi_outcome_retrieval,
    "hybrid_routed": forecast_track.predict_hybrid_routed,
}.get(_VARIANT_NAME, forecast_track.predict_single_llm)


app = FastAPI(title="The Oracles forecast agent", version="0.1.0")


class EventRequest(BaseModel):
    """Loose schema — accept everything `ai_prophet_core.forecast.schemas.Event`
    might send, with extras tolerated. The CLI sends an event dict
    indistinguishable from what `prophet forecast retrieve` writes."""

    event_ticker: str
    market_ticker: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    category: str
    rules: str | None = None
    close_time: str
    outcomes: list[str] | None = None

    class Config:
        extra = "allow"  # tolerate any future field the server adds


class OutcomeProbability(BaseModel):
    market: str  # outcome label string, must match one entry in event.outcomes
    probability: float = Field(ge=0.0, le=1.0)


class PredictionResponse(BaseModel):
    """Per the 2026-05-16 docs at https://prophetarena.co/developer:

        {"probabilities": [{"market": "<outcome>", "probability": <0..1>}, ...]}

    Each market value must match one of the event's outcomes. Probabilities
    do not have to sum to 1; the server normalizes before scoring.
    """
    probabilities: list[OutcomeProbability]
    rationale: str | None = None  # not required by spec; we keep it for logs


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "team": os.environ.get("PA_TEAM_NAME", "CanadaHacks"),
        "project": "The Oracles",
        "variant": _VARIANT_NAME,
        "version": app.version,
    }


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "The Oracles forecast agent",
        "endpoint": "POST /predict",
        "health": "GET /healthz",
    }


def _distribute_p_yes_to_outcomes(
    p_yes: float, outcomes: list[str],
) -> list[dict]:
    """Convert a single binary `p_yes` (probability outcomes[0] wins) into
    the multi-outcome `probabilities` payload the server expects.

    Strategy: outcomes[0] gets p_yes, every other outcome gets
    (1 - p_yes) / (n - 1). For 2-outcome events this is exact (YES/NO).
    For 3+ outcomes it's an uninformed split across the non-favorite
    outcomes, which is the correct expectation when our underlying variant
    only emits a single p_yes for outcomes[0]. The `multi_outcome` variant
    emits per-outcome probabilities directly and skips this step.
    """
    if not outcomes:
        return []
    n = len(outcomes)
    if n == 1:
        return [{"market": outcomes[0], "probability": p_yes}]
    remainder = max(0.0, (1.0 - p_yes) / (n - 1))
    return [
        {"market": o, "probability": (p_yes if i == 0 else remainder)}
        for i, o in enumerate(outcomes)
    ]


@app.post("/predict", response_model=PredictionResponse)
def predict(event: EventRequest) -> PredictionResponse:
    """Return per-outcome probabilities for the event.

    Contract (2026-05-16 docs):
        request:  event JSON from `prophet forecast retrieve`
        response: {"probabilities": [{"market": str, "probability": float}, ...]}

    If the variant returns a `probabilities` field (multi-outcome variants),
    use it directly; otherwise distribute the variant's single `p_yes`
    across the outcomes list for backwards compat with binary variants.
    """
    event_dict = event.model_dump()
    outcomes = event.outcomes or []
    log.info(
        "predict %s | variant=%s | outcomes=%d | title=%s",
        event.market_ticker, _VARIANT_NAME, len(outcomes), event.title[:80],
    )
    result = _VARIANT_FN(event_dict)
    p_yes = float(result["p_yes"])
    # Defensive: clamp p_yes to [0.01, 0.99] before distribution.
    p_yes = max(0.01, min(0.99, p_yes))
    rationale = str(result.get("rationale", ""))[:300]

    # Prefer per-outcome probabilities when the variant emits them
    # (multi-outcome variants per the 2026-05-16 server schema). Fall back
    # to distributing the single p_yes for legacy binary variants.
    raw_probs = result.get("probabilities")
    if (
        isinstance(raw_probs, list)
        and raw_probs
        and all(
            isinstance(p, dict) and "market" in p and "probability" in p
            for p in raw_probs
        )
    ):
        probs = [
            {
                "market": str(p["market"]),
                "probability": max(0.01, min(0.99, float(p["probability"]))),
            }
            for p in raw_probs
        ]
    else:
        probs = _distribute_p_yes_to_outcomes(p_yes, outcomes)

    log.info(
        "predict %s -> p_yes=%.3f over %d outcomes",
        event.market_ticker, p_yes, len(probs),
    )

    _PREDICTION_HISTORY.appendleft({
        "ts": datetime.now(timezone.utc).isoformat(),
        "market_ticker": event.market_ticker,
        "title": event.title,
        "category": event.category,
        "p_yes": p_yes,
        "outcomes": outcomes,
        "probabilities": probs,
        "rationale": rationale,
    })
    return PredictionResponse(
        probabilities=[OutcomeProbability(**p) for p in probs],
        rationale=rationale,
    )


@app.get("/predictions")
def predictions() -> dict[str, Any]:
    """Last 50 predictions served. Machine-readable."""
    return {
        "count": len(_PREDICTION_HISTORY),
        "predictions": list(_PREDICTION_HISTORY),
    }


def _fetch_remote_state() -> dict[str, Any]:
    """Pull endpoint + leaderboard state from Prophet Arena. Best-effort."""
    api_key = os.environ.get("PA_SERVER_API_KEY", "")
    team = os.environ.get("PA_TEAM_NAME", "CanadaHacks")
    out: dict[str, Any] = {"endpoint": None, "scores": None, "open_events": None}
    headers = {"X-API-Key": api_key}
    try:
        r = httpx.get(
            f"https://api.aiprophet.dev/forecast/endpoints/{team}",
            headers=headers, timeout=10,
        )
        out["endpoint"] = r.json() if r.status_code == 200 else {"error": r.text[:200]}
    except Exception as e:
        out["endpoint"] = {"error": str(e)[:200]}
    try:
        r = httpx.get(
            "https://api.aiprophet.dev/forecast/scores",
            headers=headers, timeout=10,
        )
        out["scores"] = r.json() if r.status_code == 200 else {"error": r.text[:200]}
    except Exception as e:
        out["scores"] = {"error": str(e)[:200]}
    try:
        r = httpx.get(
            "https://api.aiprophet.dev/forecast/events?status=open",
            headers=headers, timeout=10,
        )
        out["open_events"] = r.json() if r.status_code == 200 else {"error": r.text[:200]}
    except Exception as e:
        out["open_events"] = {"error": str(e)[:200]}
    return out


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    """Live HTML dashboard. Auto-refreshes every 30s."""
    remote = _fetch_remote_state()
    ep = remote.get("endpoint") or {}
    scores = remote.get("scores") or {}
    open_events = remote.get("open_events") or []
    if isinstance(open_events, dict) and "error" in open_events:
        open_events_list: list = []
    else:
        open_events_list = open_events if isinstance(open_events, list) else []

    rows = ""
    for p in list(_PREDICTION_HISTORY)[:20]:
        rows += (
            f"<tr><td>{html_escape(p['ts'][:19])}</td>"
            f"<td><code>{html_escape(p['market_ticker'])}</code></td>"
            f"<td>{html_escape(p['category'])}</td>"
            f"<td>{html_escape(p['title'][:80])}</td>"
            f"<td class='num'>{p['p_yes']:.3f}</td>"
            f"<td>{html_escape(p['rationale'][:120])}</td></tr>"
        )
    if not rows:
        rows = (
            "<tr><td colspan='6' style='text-align:center;color:#888'>"
            "no predictions served yet — waiting for Prophet Arena to call /predict</td></tr>"
        )

    open_rows = ""
    for e in open_events_list[:10]:
        open_rows += (
            f"<tr><td><code>{html_escape(e.get('market_ticker','?'))}</code></td>"
            f"<td>{html_escape(e.get('category','?'))}</td>"
            f"<td>{html_escape((e.get('title') or '?')[:90])}</td>"
            f"<td>{html_escape((e.get('close_time') or '?')[:19])}</td></tr>"
        )
    if not open_rows:
        open_rows = (
            "<tr><td colspan='4' style='text-align:center;color:#888'>"
            "no open events right now</td></tr>"
        )

    endpoint_pill = (
        '<span class="pill ok">ACTIVE</span>'
        if ep.get("is_active")
        else '<span class="pill bad">INACTIVE</span>'
    )
    last_run = ep.get("last_run_at") or "—"
    last_status = ep.get("last_run_status") or "—"
    last_n = ep.get("last_run_n_predictions") or "—"

    if isinstance(scores, dict) and "error" in scores:
        scores_block = f"<div class='muted'>scores fetch error: {html_escape(scores['error'][:100])}</div>"
    elif scores:
        scores_block = f"<pre>{html_escape(json.dumps(scores, indent=2)[:2000])}</pre>"
    else:
        scores_block = "<div class='muted'>no scores yet</div>"

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<title>The Oracles — dashboard</title>
<style>
  body {{ font: 13px/1.5 -apple-system, system-ui, sans-serif; max-width: 1100px;
         margin: 1.5em auto; padding: 0 1em; color: #1f2933; background: #fafbfc; }}
  h1 {{ font-size: 1.3em; margin-bottom: 0.2em; }}
  h2 {{ margin-top: 1.4em; font-size: 1.05em; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.2em; }}
  .meta {{ color: #6b7280; font-size: 0.85em; }}
  .muted {{ color: #888; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.5em 0 1em; font-size: 12px; }}
  th, td {{ text-align: left; padding: 0.35em 0.5em; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
  th {{ background: #f3f4f6; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .pill {{ display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 0.78em; font-weight: 600; }}
  .pill.ok {{ background: #d1fae5; color: #065f46; }}
  .pill.bad {{ background: #fee2e2; color: #991b1b; }}
  code {{ background: #f3f4f6; padding: 1px 5px; border-radius: 3px; font-size: 0.92em; }}
  pre {{ background: #f3f4f6; padding: 0.6em; border-radius: 6px; overflow: auto; font-size: 11px; }}
  .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.7em; margin: 0.8em 0; }}
  .summary > div {{ background: white; border: 1px solid #e5e7eb; border-radius: 6px; padding: 0.6em 0.8em; }}
  .summary .label {{ font-size: 10px; text-transform: uppercase; color: #6b7280; letter-spacing: 0.05em; }}
  .summary .value {{ font-size: 1.1em; font-weight: 600; margin-top: 0.2em; font-variant-numeric: tabular-nums; }}
</style>
</head><body>

<h1>The Oracles — live dashboard</h1>
<p class="meta">team <strong>CanadaHacks</strong> · variant served: <code>{html_escape(_VARIANT_NAME)}</code> · auto-refresh every 30s · <a href="/docs">API docs</a> · <a href="/predictions">JSON predictions</a></p>

<div class="summary">
  <div><div class="label">endpoint</div><div class="value">{endpoint_pill}</div></div>
  <div><div class="label">last call</div><div class="value">{html_escape(last_run[:19])}</div></div>
  <div><div class="label">last status</div><div class="value">{html_escape(str(last_status))}</div></div>
  <div><div class="label">last #preds</div><div class="value">{html_escape(str(last_n))}</div></div>
</div>

<h2>Open events on Prophet Arena ({len(open_events_list)})</h2>
<table>
<thead><tr><th>market_ticker</th><th>category</th><th>title</th><th>close_time</th></tr></thead>
<tbody>{open_rows}</tbody>
</table>

<h2>Recent predictions served (last {len(_PREDICTION_HISTORY)})</h2>
<table>
<thead><tr><th>timestamp</th><th>market_ticker</th><th>category</th><th>title</th><th>p_yes</th><th>rationale</th></tr></thead>
<tbody>{rows}</tbody>
</table>

<h2>Scores</h2>
{scores_block}

<h2>Local quick links</h2>
<ul>
<li><a href="/healthz">/healthz</a> — server health JSON</li>
<li><a href="/docs">/docs</a> — Swagger UI</li>
<li><a href="/redoc">/redoc</a> — ReDoc API spec</li>
<li><a href="/predictions">/predictions</a> — last 50 predictions JSON</li>
</ul>

</body></html>"""


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("PROPHET_AGENT_HOST", "0.0.0.0")
    # Railway sets PORT dynamically; fall back to our local default.
    port = int(
        os.environ.get("PORT") or os.environ.get("PROPHET_AGENT_PORT") or "8000"
    )
    log.info(
        "Starting Oracles agent on %s:%d (variant=%s)",
        host, port, _VARIANT_NAME,
    )
    uvicorn.run(app, host=host, port=port)
