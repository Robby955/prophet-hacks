"""FastAPI agent server for Prophet Hacks forecasting track.

The Prophet Arena server pulls predictions from a registered HTTP endpoint
when new events appear. We wrap a `predict_*` variant from
`forecast_track.py` behind a /predict route and expose it publicly through
Railway at https://agent.forecastingpath.com.

Usage:
    # 1. Start the server:
    .venv/bin/python forecast_agent_server.py
    # listens on 0.0.0.0:8000

    # 2. Register the endpoint with Prophet Arena (one-shot):
    .venv/bin/prophet forecast register \\
        --team-name CanadaHacks \\
        --endpoint-url https://agent.forecastingpath.com/predict

Health checks:
    curl http://localhost:8000/healthz       -> {"status":"ok","variant":"..."}
    curl -X POST http://localhost:8000/predict -H 'content-type: application/json' \\
         -d '{"event_ticker":"TEST","market_ticker":"TEST","title":"Will p=0.5?","category":"Test","close_time":"2099-01-01T00:00:00Z","outcomes":["Yes","No"]}'

Variant routing:
    Set PROPHET_AGENT_VARIANT env var to swap which predict_* in
    forecast_track.py gets called. The local fallback is single_llm;
    production currently sets `multi_outcome_retrieval`, which uses Brave
    Search evidence plus the multi-outcome Opus 4.7 prompt with market-odds
    anchoring (Phase 2, 2026-05-16).

Response schema (per the 2026-05-16 server docs):
    {"probabilities": [{"market": "<outcome>", "probability": <0..1>}, ...]}

For legacy single-`p_yes` variants the server distributes p_yes across
outcomes (outcomes[0] gets p_yes, the rest evenly share 1-p_yes). For the
`multi_outcome` variant the per-outcome probabilities are taken straight
from the model.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from html import escape as html_escape
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()

import forecast_track  # noqa: E402
from evaluation.ece import (  # noqa: E402
    expected_calibration_error,
    reliability_diagram_data,
)


# In-memory ring buffer of the last N predictions served. Used by /dashboard.
_PREDICTION_HISTORY: collections.deque = collections.deque(maxlen=50)
_SERVER_START_TS = datetime.now(timezone.utc)
_TOTAL_PREDICTIONS = 0
_TOTAL_COST_USD = 0.0
_ERROR_COUNT = 0

# Predictions-per-minute history for the dashboard sparkline. Each tick is
# one minute. Counter resets each minute via the /predict path.
_PREDICTIONS_PER_MIN: collections.deque = collections.deque(maxlen=30)
_PPM_CURRENT_MINUTE: int = -1
_PPM_CURRENT_COUNT: int = 0

# SSE subscribers: asyncio.Queues that get an event each time /predict runs.
_SSE_SUBSCRIBERS: list = []

# Short-lived dashboard demo runs. These are intentionally in-memory: the
# demo is an operational view, not a persisted prediction record.
_DEMO_RUNS: dict[str, dict[str, Any]] = {}
_DEMO_RUN_LOCK = threading.Lock()
_DEMO_MAX_RUNS = 20
_DEMO_MAX_ACTIVE_RUNS = 2

# Approximate per-event cost in USD for each variant. Used for spend tracking.
_VARIANT_COSTS: dict[str, float] = {
    "uniform_prior": 0.0,
    "single_llm": 0.005,
    "opus_47": 0.015,
    "opus_46": 0.015,
    "gpt55": 0.010,
    "gpt52": 0.010,
    "ensemble_logit": 0.015,
    "ensemble_leaderboard": 0.025,
    "sonnet_cot": 0.007,
    "sonnet_cot_shrink": 0.007,
    "multi_outcome": 0.010,
    "multi_outcome_sc3": 0.030,
    "multi_outcome_retrieval": 0.10,
    "multi_outcome_retrieval_sae": 0.10,
    "hybrid_routed": 0.008,
}

# Short one-line description for each variant, shown on the dashboard's
# variant card so anyone landing on the page knows what we are serving.
_VARIANT_DESCRIPTIONS: dict[str, str] = {
    "uniform_prior": "Deterministic 1/n_outcomes. Free control baseline; no LLM calls.",
    "single_llm": "One Anthropic Sonnet 4.6 call per event. Emits binary p_yes for outcomes[0]; server distributes across the outcomes list.",
    "opus_47": "One Claude Opus 4.7 call. Stronger reasoner, ~3x cost; underperformed on our 26-event backtest.",
    "opus_46": "One Claude Opus 4.6 call. Leaderboard top agent reference; underperformed on our small sample.",
    "gpt55": "One OpenAI GPT-5.5 call. Cross-vendor sanity check.",
    "gpt52": "One OpenAI GPT-5.2 call. Top OpenAI fixed-context model on the public leaderboard.",
    "ensemble_logit": "Sonnet 4.6 + GPT-5.5 logit-mean blend. Cross-vendor diversity.",
    "ensemble_leaderboard": "Three-way logit-mean of Sonnet 4.6 + Opus 4.6 + GPT-5.2.",
    "sonnet_cot": "Structured chain-of-thought JSON prompt with backward-check + verification step. Tested negative on small sample.",
    "sonnet_cot_shrink": "sonnet_cot + post-hoc shrinkage toward the uninformed prior on low-conviction outputs.",
    "multi_outcome": "ONE Sonnet 4.6 call returns per-outcome probabilities directly (no distribute hack). Kalshi longshot guard applied.",
    "multi_outcome_sc3": "k=3 parallel multi_outcome calls, averaged per-outcome (self-consistency).",
    "multi_outcome_retrieval": "Brave Search → 5 deduped evidence chunks → Opus 4.7 multi-outcome call with market-odds anchoring → Kalshi longshot guard. The current production variant.",
    "multi_outcome_retrieval_sae": "Offline experimental: production retrieval path plus borrowed-strength shrinkage toward the 1/n prior before the Kalshi longshot guard.",
    "hybrid_routed": "Binary (n<=2): gpt55. Multi (n>2): multi_outcome. Routes by outcome count to play each model's strength.",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("oracles.agent")


def _prediction_store_path() -> Path:
    """Append-only JSONL store for served predictions.

    Defaults to ignored local `logs/` so tests and local runs do not dirty the
    repo. Railway can opt into a mounted volume or explicit path without code
    changes.
    """
    explicit = os.environ.get("PROPHET_PREDICTION_STORE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if volume:
        return Path(volume) / "forecastingpath" / "predictions.jsonl"
    return Path(__file__).resolve().parent / "logs" / "live_predictions.jsonl"


def _append_prediction_record(record: dict[str, Any], path: Path | None = None) -> None:
    """Persist one prediction record. Never fail /predict because disk failed."""
    store_path = path or _prediction_store_path()
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with store_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.warning("prediction persistence failed: %s", str(exc)[:160])


def _load_prediction_history_from_disk(
    path: Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Load latest persisted predictions, newest first.

    Malformed lines are ignored so one bad write cannot break the dashboard.
    """
    store_path = path or _prediction_store_path()
    if not store_path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = store_path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        log.warning("prediction persistence read failed: %s", str(exc)[:160])
        return []
    for line in lines[-max(limit * 3, limit):]:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return list(reversed(records[-limit:]))


def _prediction_store_count(path: Path | None = None) -> int:
    store_path = path or _prediction_store_path()
    if not store_path.exists():
        return 0
    count = 0
    try:
        with store_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    if isinstance(json.loads(line), dict):
                        count += 1
                except json.JSONDecodeError:
                    continue
    except Exception:
        return 0
    return count


def _prediction_history_snapshot(limit: int = 50) -> list[dict[str, Any]]:
    """Recent predictions from memory, lazily restored from disk if empty."""
    if not _PREDICTION_HISTORY:
        for record in reversed(_load_prediction_history_from_disk(limit=limit)):
            _PREDICTION_HISTORY.appendleft(record)
    return list(_PREDICTION_HISTORY)[:limit]


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
    "multi_outcome_retrieval_sae": forecast_track.predict_multi_outcome_retrieval_sae,
    "hybrid_routed": forecast_track.predict_hybrid_routed,
}.get(_VARIANT_NAME, forecast_track.predict_single_llm)


app = FastAPI(
    title="The Oracles forecast agent",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

class _AuthGatedStaticFiles(StaticFiles):
    """Serve public assets while keeping research HTML behind dashboard auth."""

    _PROTECTED_PATHS = {
        "/static/abstain_slider.html",
        "/static/bootstrap_hist.html",
        "/static/summary.html",
        "/static/status.html",
        "/static/gallery_open.html",
        "/static/gallery_resolved.html",
        "/static/heatmap_resolved.html",
        "/static/pipeline_trace.html",
        "/static/scatter_resolved.html",
    }

    @staticmethod
    def _scope_headers(scope: dict[str, Any]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for raw_key, raw_value in scope.get("headers", []):
            key = raw_key.decode("latin1").lower()
            value = raw_value.decode("latin1")
            headers[key] = value
        return headers

    @staticmethod
    def _dashboard_token_from_scope(scope: dict[str, Any]) -> str:
        headers = _AuthGatedStaticFiles._scope_headers(scope)
        query = parse_qs((scope.get("query_string") or b"").decode("latin1"))
        query_token = (query.get("token") or [""])[0]
        if query_token:
            return query_token
        header_token = headers.get("x-dashboard-token", "")
        if header_token:
            return header_token
        auth_header = headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()
        cookie = SimpleCookie()
        cookie.load(headers.get("cookie", ""))
        morsel = cookie.get("dashboard_token")
        return morsel.value if morsel else ""

    @classmethod
    def _is_static_dashboard_authorized(cls, scope: dict[str, Any]) -> bool:
        expected = os.environ.get("DASHBOARD_AUTH_TOKEN", "").strip()
        if not expected:
            return True
        supplied = cls._dashboard_token_from_scope(scope)
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        path = str(scope.get("path") or "")
        if path in self._PROTECTED_PATHS and not self._is_static_dashboard_authorized(scope):
            headers = self._scope_headers(scope)
            if "text/html" in headers.get("accept", "").lower() and os.environ.get("DASHBOARD_PIN", "").strip():
                response = RedirectResponse(
                    f"/login?next={quote(path, safe='')}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            else:
                response = Response(
                    "dashboard authentication required",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            await response(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


# Mount /static for favicon, OG image, architecture diagram. Static research
# HTML is auth-gated by _AuthGatedStaticFiles while assets stay public.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", _AuthGatedStaticFiles(directory=str(_STATIC_DIR)), name="static")

# OpenAI-compatible /v1/chat/completions adapter for PA's general onboarding
# at prophetarena.co/onboarding. Implemented as a standalone router so the
# forecasting path (/predict) and the chat-completions path stay decoupled.
# Keep this import + include_router line minimal; do not move logic inline.
from chat_completions_adapter import router as _chat_router  # noqa: E402
app.include_router(_chat_router)


class EventRequest(BaseModel):
    """Loose schema — accept everything `ai_prophet_core.forecast.schemas.Event`
    might send, with extras tolerated. The CLI sends an event dict
    indistinguishable from what `prophet forecast retrieve` writes."""

    model_config = ConfigDict(extra="allow")

    event_ticker: str
    market_ticker: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    category: str
    rules: str | None = None
    close_time: str
    outcomes: list[str] | None = None


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


def _build_commit_sha() -> str:
    """First short SHA we find from deployment metadata.

    `railway up` file uploads do not reliably expose a git SHA in the runtime,
    so `scripts/agent/deploy.sh` persists `PROPHET_BUILD_COMMIT_SHA` as a
    non-secret Railway variable before deploy. Fall back to Railway's own git
    env var, a local `.commit_sha`, then local git. Returns 'dev' if none found.

    Surfaced on /healthz so anyone (curl, Codex, another agent, Rob)
    can verify which code is live without Railway dashboard access.
    Fixes a class of "is the deploy actually current?" confusion that
    burned an hour 2026-05-16.
    """
    env_sha = os.environ.get("PROPHET_BUILD_COMMIT_SHA", "").strip()
    if env_sha:
        return env_sha[:8]
    env_sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
    if env_sha:
        return env_sha[:8]
    try:
        with open(".commit_sha") as f:
            sha = f.read().strip()
            if sha:
                return sha[:8]
    except (FileNotFoundError, OSError):
        pass
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "dev"


_BUILD_COMMIT_SHA: str = _build_commit_sha()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "team": os.environ.get("PA_TEAM_NAME", "CanadaHacks"),
        "project": "The Oracles",
        "variant": _VARIANT_NAME,
        "version": app.version,
        "commit": _BUILD_COMMIT_SHA,
    }


def _dashboard_auth_token() -> str:
    return os.environ.get("DASHBOARD_AUTH_TOKEN", "").strip()


def _dashboard_auth_enabled() -> bool:
    return bool(_dashboard_auth_token())


def _request_dashboard_token(request: Request) -> str:
    query_token = request.query_params.get("token", "")
    if query_token:
        return query_token
    header_token = request.headers.get("x-dashboard-token", "")
    if header_token:
        return header_token
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    cookie_token = request.cookies.get("dashboard_token", "")
    return cookie_token


def _is_dashboard_authorized(request: Request) -> bool:
    expected = _dashboard_auth_token()
    if not expected:
        return True
    supplied = _request_dashboard_token(request)
    return bool(supplied) and secrets.compare_digest(supplied, expected)


def _require_dashboard_auth(request: Request) -> None:
    if _is_dashboard_authorized(request):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="dashboard authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _dashboard_pin() -> str:
    """Optional shareable PIN. Visitors enter this PIN on /login and get
    issued the DASHBOARD_AUTH_TOKEN cookie. Lets Rob share the dashboard
    URL without putting a long random token in URLs.

    Set DASHBOARD_PIN env var on Railway. Recommended: 6-8 digit numeric,
    rotated periodically.
    """
    return os.environ.get("DASHBOARD_PIN", "").strip()


def _is_safe_redirect(target: str) -> bool:
    """Allow only same-origin relative paths to prevent open-redirect bugs."""
    if not target:
        return False
    if not target.startswith("/"):
        return False
    if target.startswith("//"):  # protocol-relative
        return False
    return True


def _wants_html(request: Request) -> bool:
    """Heuristic: browser navigations send Accept: text/html; API clients
    typically do not. Used to choose between a /login redirect (HTML) and
    a 401 JSON response (API)."""
    accept = request.headers.get("accept", "")
    return "text/html" in accept.lower()


def _require_dashboard_auth_redirect(request: Request) -> None:
    """Browser-friendly variant: if not authorized, redirect to /login
    with the current path so the user can enter a PIN. Falls back to 401
    for API callers (Accept != text/html).

    Use this on HTML routes (/dashboard, /compare, /compare-open).
    Use plain `_require_dashboard_auth` on JSON routes (/predictions, /events).
    """
    if _is_dashboard_authorized(request):
        return
    if _wants_html(request) and _dashboard_pin():
        # Bounce to /login. The handler will read ?next=... and round-trip.
        target = str(request.url.path)
        if request.url.query:
            target += "?" + request.url.query
        from urllib.parse import quote
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/login?next={quote(target, safe='')}"},
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="dashboard authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _set_dashboard_cookie_if_needed(response: Response, request: Request) -> None:
    expected = _dashboard_auth_token()
    supplied = request.query_params.get("token", "")
    if not expected or not supplied:
        return
    if not secrets.compare_digest(supplied, expected):
        return
    response.set_cookie(
        "dashboard_token",
        supplied,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=True,
        samesite="lax",
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root() -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForecastingPath</title>
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" sizes="192x192" href="/static/icon-192.png">
<meta name="description" content="ForecastingPath is a live forecasting endpoint, trace viewer, and results desk for Prophet Hacks 2026.">
<meta property="og:title" content="ForecastingPath">
<meta property="og:description" content="Live forecasting endpoint, trace viewer, and results desk.">
<meta property="og:image" content="https://forecastingpath.com/static/banner.webp">
<meta property="og:url" content="https://forecastingpath.com">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://forecastingpath.com/static/banner.webp">
<style>
  :root {{
    --bg: #f4f7fb;
    --panel: #ffffff;
    --line: #d9e0ea;
    --text: #0f172a;
    --muted: #64748b;
    --accent: #2242ff;
    --accent-soft: #ecf2ff;
    --ok: #047857;
    --ink: #111827;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background:
         linear-gradient(180deg, #ffffff 0%, var(--bg) 58%, #eef2f7 100%);
         color: var(--text); font: 16px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif; }}
  body::before {{ content: ""; position: fixed; inset: 0; pointer-events: none; opacity: 0.42;
         background-image:
           linear-gradient(rgba(15,23,42,0.045) 1px, transparent 1px),
           linear-gradient(90deg, rgba(15,23,42,0.035) 1px, transparent 1px);
         background-size: 56px 56px; mask-image: linear-gradient(180deg, #000 0%, transparent 72%); }}
  .shell {{ min-height: 100svh; display: grid; grid-template-rows: auto 1fr auto; overflow-x: clip; }}
  header {{ width: min(1180px, calc(100% - 48px)); margin: 0 auto; padding: 22px 0;
            display: flex; align-items: center; justify-content: space-between; gap: 1rem; }}
  .brand {{ display: inline-flex; align-items: center; gap: 0.72rem; min-width: 0;
            color: var(--text); text-decoration: none; font-weight: 760; }}
  .brand img {{ width: 34px; height: 34px; border-radius: 8px; flex: 0 0 auto; }}
  .brand span {{ overflow-wrap: anywhere; }}
  nav {{ display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap; justify-content: flex-end; }}
  nav a {{ color: var(--muted); text-decoration: none; font-size: 0.92rem; font-weight: 650;
           padding: 0.4rem 0.6rem; border-radius: 8px; transition: color 160ms ease, background 160ms ease; }}
  nav a:hover {{ color: var(--text); background: rgba(255,255,255,0.74); }}
  .status-pill {{ display: inline-flex; align-items: center; gap: 0.55rem; min-height: 34px;
                  padding: 0 0.72rem; border: 1px solid var(--line); border-radius: 999px;
                  background: rgba(255,255,255,0.72); color: var(--muted); font-size: 0.9rem;
                  white-space: nowrap; }}
  .dot {{ width: 8px; height: 8px; border-radius: 99px; background: var(--ok); display: inline-block;
          box-shadow: 0 0 0 5px rgba(4,120,87,0.10); animation: pulse 2.4s ease-in-out infinite; }}
  main {{ width: min(1180px, calc(100% - 48px)); margin: 0 auto; padding: clamp(28px, 5vw, 58px) 0 64px; position: relative; }}
  .hero-shell {{ display: grid; grid-template-columns: minmax(0, 0.9fr) minmax(390px, 1.1fr);
                 gap: clamp(36px, 6vw, 86px); align-items: center; }}
  .hero-copy {{ min-width: 0; max-width: 620px; }}
  .eyebrow {{ margin: 0 0 0.85rem; font-size: 0.82rem; font-weight: 780; letter-spacing: 0.12em;
              text-transform: uppercase; color: var(--muted); }}
  .hero-title {{ margin: 0; max-width: none; font-size: clamp(2.6rem, 4.4vw, 4.05rem);
                 line-height: 0.94; letter-spacing: 0; overflow-wrap: normal; }}
  .lead {{ max-width: 520px; margin: 1.1rem 0 0; color: var(--muted);
           font-size: clamp(1.02rem, 1.7vw, 1.18rem); }}
  .actions {{ display: flex; flex-wrap: wrap; gap: 0.8rem; margin-top: 1.55rem; }}
  .btn {{ display: inline-flex; align-items: center; justify-content: center;
          min-height: 44px; padding: 0 1rem; border-radius: 8px;
          font-weight: 720; text-decoration: none; border: 1px solid var(--line);
          transition: transform 160ms ease, border-color 160ms ease, background 160ms ease; }}
  .btn:hover {{ transform: translateY(-1px); }}
  .primary {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .secondary {{ background: rgba(255,255,255,0.82); color: var(--text); }}
  .run-window {{ min-width: 0; width: 100%; background: rgba(255,255,255,0.9);
                 border: 1px solid var(--line); border-radius: 8px; overflow: hidden;
                 box-shadow: 0 24px 70px rgba(15,23,42,0.12); }}
  .run-top {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem;
              padding: 0.85rem 1rem; border-bottom: 1px solid #e5ebf4; color: var(--muted);
              font-size: 0.86rem; font-weight: 680; }}
  .lights {{ display: flex; gap: 0.34rem; }}
  .lights span {{ width: 8px; height: 8px; border-radius: 99px; background: #cbd5e1; }}
  .run-body {{ padding: 1.1rem; display: grid; gap: 1rem; }}
  .stage-list {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.72rem; }}
  .stage {{ min-height: 132px; border: 1px solid #e5ebf4; border-radius: 8px; padding: 0.82rem;
            background: linear-gradient(180deg, #fff 0%, #f8fafc 100%);
            display: flex; flex-direction: column; justify-content: space-between; position: relative; overflow: hidden; }}
  .stage::after {{ content: ""; position: absolute; left: -30%; right: -30%; top: 0; height: 2px;
                   background: linear-gradient(90deg, transparent, var(--accent), transparent);
                   transform: translateX(-80%); animation: scan 3.2s ease-in-out infinite; opacity: 0.9; }}
  .stage:nth-child(2)::after {{ animation-delay: 0.45s; }}
  .stage:nth-child(3)::after {{ animation-delay: 0.9s; }}
  .stage:nth-child(4)::after {{ animation-delay: 1.35s; }}
  .stage small {{ color: var(--muted); font-weight: 760; letter-spacing: 0.08em; }}
  .stage strong {{ display: block; margin-top: 0.42rem; font-size: 1rem; }}
  .stage span {{ color: var(--muted); font-size: 0.9rem; }}
  .probability {{ display: grid; gap: 0.58rem; padding: 1rem; border: 1px solid #e5ebf4; border-radius: 8px;
                  background: #0f172a; color: #dbeafe; }}
  .prob-head {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; font-weight: 720; }}
  .prob-head span:last-child {{ color: #8dd7b7; }}
  .bars {{ display: grid; gap: 0.46rem; }}
  .bar {{ height: 9px; border-radius: 99px; background: rgba(219,234,254,0.14); overflow: hidden; }}
  .bar i {{ display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #8dd7b7, #9fb6ff); animation: settle 2.8s ease-in-out infinite; }}
  .status-panel {{ min-width: 0; width: 100%; background: rgba(255,255,255,0.78);
                   border: 1px solid var(--line); border-radius: 8px; padding: 1rem; }}
  .status-panel h2 {{ margin: 0 0 0.85rem; font-size: 0.86rem; text-transform: uppercase;
                      letter-spacing: 0.1em; color: var(--muted); }}
  .rows {{ display: grid; gap: 0.3rem; }}
  .row {{ display: flex; align-items: center; justify-content: space-between;
          gap: 1rem; padding: 0.8rem 0; border-top: 1px solid #e7edf5; }}
  .row:first-child {{ border-top: 0; }}
  .row span:first-child {{ color: var(--muted); }}
  .row strong {{ text-align: right; overflow-wrap: anywhere; }}
  .lower {{ margin-top: clamp(42px, 6vw, 74px); display: grid; gap: 22px; }}
  .demo-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
  .demo-link {{ display: grid; align-content: space-between; min-height: 148px; padding: 1rem;
                border: 1px solid var(--line); border-radius: 8px; background: rgba(255,255,255,0.76);
                color: var(--text); text-decoration: none; transition: transform 160ms ease, border-color 160ms ease, background 160ms ease; }}
  .demo-link:hover {{ transform: translateY(-2px); border-color: #aebced; background: #fff; }}
  .demo-link span {{ color: var(--muted); font-size: 0.78rem; font-weight: 780; letter-spacing: 0.1em; text-transform: uppercase; }}
  .demo-link strong {{ display: block; margin-top: 0.7rem; font-size: 1.05rem; }}
  .demo-link em {{ margin-top: 0.45rem; color: var(--muted); font-style: normal; font-size: 0.92rem; }}
  footer {{ width: min(1180px, calc(100% - 48px)); margin: 0 auto; padding: 24px 0;
            color: var(--muted); font-size: 0.92rem; display: flex; justify-content: space-between; gap: 1rem; }}
  footer a {{ color: var(--accent); text-decoration: none; font-weight: 650; }}
  @keyframes pulse {{
    0%, 100% {{ box-shadow: 0 0 0 5px rgba(4,120,87,0.10); }}
    50% {{ box-shadow: 0 0 0 9px rgba(4,120,87,0.04); }}
  }}
  @keyframes scan {{
    0%, 22% {{ transform: translateX(-80%); opacity: 0; }}
    42%, 80% {{ opacity: 1; }}
    100% {{ transform: translateX(80%); opacity: 0; }}
  }}
  @keyframes settle {{
    0%, 100% {{ transform: scaleX(0.96); transform-origin: left; }}
    50% {{ transform: scaleX(1); transform-origin: left; }}
  }}
  @media (max-width: 920px) {{
    header, footer {{ align-items: flex-start; flex-direction: column; }}
    nav {{ justify-content: flex-start; }}
    .hero-shell {{ grid-template-columns: 1fr; }}
    .run-window {{ max-width: 680px; }}
    .demo-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  }}
  @media (min-width: 921px) and (max-height: 560px) {{
    header {{ padding: 14px 0; }}
    main {{ padding-top: 22px; padding-bottom: 36px; }}
    .hero-title {{ font-size: clamp(2.5rem, 4vw, 3.75rem); }}
    .lead {{ margin-top: 0.95rem; font-size: 1rem; }}
    .actions {{ margin-top: 1.2rem; }}
    .stage {{ min-height: 104px; }}
    .run-body {{ gap: 0.7rem; padding: 0.86rem; }}
    .status-panel {{ display: none; }}
  }}
  @media (max-width: 520px) {{
    header, main, footer {{ width: min(100% - 32px, 1180px); }}
    main {{ padding-top: 24px; }}
    .status-pill {{ white-space: normal; }}
    .hero-title {{ font-size: 2.35rem; }}
    .lead {{ margin-top: 1rem; font-size: 1rem; }}
    .actions {{ margin-top: 1.2rem; flex-direction: column; align-items: stretch; }}
    .stage-list {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .demo-grid {{ grid-template-columns: 1fr; }}
    .stage {{ min-height: 94px; }}
    .status-panel {{ padding: 0.95rem; }}
    .row {{ padding: 0.6rem 0; }}
    .demo-link {{ min-height: 118px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{ animation: none !important; transition: none !important; }}
  }}
</style>
</head><body>
<div class="shell">
<header>
  <a class="brand" href="/" aria-label="ForecastingPath home"><img src="/static/flaviconlogo.webp" alt=""><span>ForecastingPath</span></a>
  <nav aria-label="Primary">
    <a href="#run">Run loop</a>
    <a href="#views">Views</a>
    <a href="/login?next=/dashboard">Console</a>
    <span class="status-pill"><span class="dot"></span><span>endpoint online</span></span>
  </nav>
</header>
<main>
  <section class="hero-shell" aria-label="ForecastingPath overview">
    <div class="hero-copy">
      <p class="eyebrow">Prophet Hacks 2026</p>
      <h1 class="hero-title">ForecastingPath</h1>
      <p class="lead">Forecasts, run history, and experiment views in one place.</p>
      <div class="actions">
        <a class="btn primary" href="/static/summary.pdf">Read the report</a>
        <a class="btn secondary" href="/login?next=/dashboard">Open console</a>
        <a class="btn secondary" href="/login?next=/observatory">View observatory</a>
      </div>
    </div>
    <div class="run-window" id="run" aria-label="Pipeline preview">
      <div class="run-top"><div class="lights"><span></span><span></span><span></span></div><span>live run preview</span></div>
      <div class="run-body">
        <div class="stage-list">
          <div class="stage"><div><small>01</small><strong>Event</strong></div><span>payload in</span></div>
          <div class="stage"><div><small>02</small><strong>Evidence</strong></div><span>sources ranked</span></div>
          <div class="stage"><div><small>03</small><strong>Forecast</strong></div><span>probabilities set</span></div>
          <div class="stage"><div><small>04</small><strong>Submit</strong></div><span>response logged</span></div>
        </div>
        <div class="probability" aria-label="Probability vector preview">
          <div class="prob-head"><span>Probability vector</span><span>ready</span></div>
          <div class="bars"><div class="bar"><i style="width:62%"></i></div><div class="bar"><i style="width:38%"></i></div><div class="bar"><i style="width:18%"></i></div></div>
        </div>
      </div>
    </div>
  </section>
  <section class="lower" id="views" aria-label="ForecastingPath views">
    <div class="status-panel" aria-label="Public status">
      <h2>Public status</h2>
      <div class="rows">
        <div class="row"><span>Endpoint</span><strong>healthy</strong></div>
        <div class="row"><span>Submission</span><strong>active</strong></div>
        <div class="row"><span>Demo console</span><strong>ready</strong></div>
        <div class="row"><span>Result views</span><strong>loaded</strong></div>
      </div>
    </div>
    <div class="demo-grid">
      <a class="demo-link" href="/login?next=/dashboard"><span>Demo</span><strong>Pipeline console</strong><em>Run the staged forecast demo and watch the live feed.</em></a>
      <a class="demo-link" href="/login?next=/observatory"><span>Ops</span><strong>Observatory</strong><em>Current commit, run history, and first-call review.</em></a>
      <a class="demo-link" href="/login?next=/static/abstain_slider.html"><span>Strategy</span><strong>Confidence slider</strong><em>Move the threshold and see how scoring changes.</em></a>
      <a class="demo-link" href="/login?next=/static/scatter_resolved.html"><span>Results</span><strong>Event map</strong><em>Resolved-event losses, comparisons, and drilldowns.</em></a>
    </div>
  </section>
</main>
<footer>
  <div>Team CanadaHacks · Project The Oracles</div>
  <div><a href="/healthz">health</a> &middot; <a href="/login?next=/review">brief</a> &middot; <a href="/login?next=/dashboard">dashboard</a></div>
</footer>
</div>

</body></html>"""


@app.get("/observatory", response_class=HTMLResponse)
def observatory(
    request: Request,
    _: None = Depends(_require_dashboard_auth_redirect),
) -> HTMLResponse:
    """Auth-gated research and operations console.

    This page intentionally contains details that the public landing omits
    during active scoring: exact variant, model-decision notes, scoring
    caveats, and operational failure modes.
    """
    history = _prediction_history_snapshot()
    prediction_count = len(history)
    persisted_count = _prediction_store_count()
    last_prediction = history[0] if history else None
    last_title = str(last_prediction.get("title") or last_prediction.get("market_ticker") or "") if last_prediction else "waiting for first Prophet Arena call"
    last_latency = "not observed yet"
    if last_prediction:
        trace = last_prediction.get("trace") if isinstance(last_prediction.get("trace"), dict) else {}
        latency = trace.get("latency_ms") if isinstance(trace, dict) else {}
        if isinstance(latency, dict) and latency.get("total") is not None:
            last_latency = f"{int(latency.get('total', 0))} ms"

    recent_rows = ""
    for row in history[:10]:
        trace = row.get("trace") if isinstance(row.get("trace"), dict) else {}
        latency = trace.get("latency_ms") if isinstance(trace, dict) else {}
        total_latency = "—"
        if isinstance(latency, dict) and latency.get("total") is not None:
            total_latency = f"{int(latency.get('total', 0))} ms"
        parse_path = str(trace.get("parse_path") or trace.get("parser_path") or trace.get("parse") or "—")
        warnings_raw = trace.get("warnings") if isinstance(trace, dict) else None
        if isinstance(warnings_raw, list):
            warnings = "; ".join(str(w) for w in warnings_raw[:3]) or "—"
        elif warnings_raw:
            warnings = str(warnings_raw)
        else:
            warnings = "—"
        probs = row.get("probabilities") if isinstance(row.get("probabilities"), list) else []
        prob_label = "—"
        if probs:
            pairs = []
            for p in probs[:3]:
                if not isinstance(p, dict):
                    continue
                try:
                    pairs.append(f"{p.get('market', '?')}: {float(p.get('probability', 0.0)):.2f}")
                except (TypeError, ValueError):
                    pairs.append(f"{p.get('market', '?')}: ?")
            prob_label = ", ".join(pairs) if pairs else "—"
        recent_rows += (
            "<tr>"
            f"<td><code>{html_escape(str(row.get('market_ticker', '?')))}</code><br><span class='small muted'>{html_escape(str(row.get('ts', ''))[:19])}</span></td>"
            f"<td>{html_escape(str(row.get('title', '?'))[:96])}<br><span class='small muted'>{html_escape(str(row.get('category', '?')))}</span></td>"
            f"<td class='small'>{html_escape(prob_label)}</td>"
            f"<td class='num nowrap'>{html_escape(total_latency)}</td>"
            f"<td>{html_escape(parse_path)}</td>"
            f"<td class='small'>{html_escape(warnings)}</td>"
            "</tr>"
        )
    if not recent_rows:
        recent_rows = (
            "<tr><td colspan='6' class='muted' style='text-align:center;padding:18px'>"
            "No Prophet Arena calls recorded yet. The first call will add a row here with probabilities, "
            "latency, parser path, warnings, and evidence coverage."
            "</td></tr>"
        )

    rows = [
        ("Live commit", _BUILD_COMMIT_SHA),
        ("Production variant", _VARIANT_NAME),
        ("Predictions in memory", str(prediction_count)),
        ("Persisted records", str(persisted_count)),
        ("Last PA call", last_title[:90]),
        ("Last total latency", last_latency),
        ("Server uptime", _uptime_human()),
    ]
    state_rows = "\n".join(
        f"<div class='kv'><span>{html_escape(k)}</span><strong>{html_escape(v)}</strong></div>"
        for k, v in rows
    )
    response = HTMLResponse(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForecastingPath Observatory</title>
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<style>
  :root {{
    --bg: #f6f8fb; --panel: #fff; --ink: #101828; --muted: #667085;
    --line: #dbe3ef; --accent: #2146ff; --good: #047857; --warn: #b45309;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; color: var(--ink); background: var(--bg);
          font: 15px/1.52 -apple-system, "Segoe UI", system-ui, sans-serif; }}
  header {{ position: sticky; top: 0; z-index: 2; background: rgba(246,248,251,0.92);
            backdrop-filter: blur(12px); border-bottom: 1px solid var(--line); }}
  .bar {{ max-width: 1240px; margin: 0 auto; padding: 16px 22px;
          display: flex; align-items: center; justify-content: space-between; gap: 18px; }}
  .brand {{ display: flex; align-items: center; gap: 10px; font-weight: 760; }}
  .brand img {{ width: 34px; height: 34px; border-radius: 8px; }}
  nav {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  nav a {{ color: var(--muted); text-decoration: none; font-weight: 650; font-size: 0.92rem; }}
  main {{ max-width: 1240px; margin: 0 auto; padding: 28px 22px 54px; }}
  h1 {{ margin: 0; font-size: clamp(2.1rem, 5vw, 3.35rem); line-height: 0.98; letter-spacing: 0; }}
  .lead {{ margin: 14px 0 0; max-width: 760px; color: var(--muted); font-size: 1.06rem; }}
  section {{ margin-top: 28px; }}
  .grid {{ display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; }}
  .span4 {{ grid-column: span 4; }} .span5 {{ grid-column: span 5; }} .span7 {{ grid-column: span 7; }} .span12 {{ grid-column: span 12; }}
  h2 {{ margin: 0 0 12px; font-size: 1rem; text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); }}
  .kv {{ display: flex; align-items: baseline; justify-content: space-between; gap: 18px; padding: 9px 0; border-top: 1px solid #edf1f7; }}
  .kv:first-of-type {{ border-top: 0; }}
  .kv span {{ color: var(--muted); }} .kv strong {{ text-align: right; overflow-wrap: anywhere; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 10px 8px; text-align: left; border-bottom: 1px solid #edf1f7; vertical-align: top; }}
  th {{ color: var(--muted); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.06em; }}
  td.num {{ font-variant-numeric: tabular-nums; font-weight: 700; }}
  .small {{ font-size: 0.86rem; }} .muted {{ color: var(--muted); }} .nowrap {{ white-space: nowrap; }}
  .ok {{ color: var(--good); }} .warn {{ color: var(--warn); }}
  .eyebrow {{ color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.82rem; font-weight: 760; margin-bottom: 10px; }}
  .callout {{ border-left: 4px solid var(--accent); background: #eef2ff; padding: 14px 16px; border-radius: 8px; }}
  .callout p {{ margin: 0; }}
  ul {{ margin: 0; padding-left: 1.1rem; }} li {{ margin: 0.35rem 0; }}
  code {{ background: #eef2f7; border: 1px solid #dbe3ef; padding: 0.08rem 0.28rem; border-radius: 4px; }}
  footer {{ max-width: 1240px; margin: 0 auto; padding: 0 22px 30px; color: var(--muted); }}
  a {{ color: var(--accent); }}
  @media (max-width: 860px) {{
    .bar {{ align-items: flex-start; flex-direction: column; }}
    .span4, .span5, .span7, .span12 {{ grid-column: 1 / -1; }}
    h1 {{ font-size: 2.75rem; }}
  }}
  @media (max-width: 420px) {{
    h1 {{ font-size: 2.25rem; }}
  }}
</style>
</head><body>
<header>
  <div class="bar">
    <div class="brand"><img src="/static/flaviconlogo.webp" alt="ForecastingPath"><span>ForecastingPath Observatory</span></div>
    <nav>
      <a href="#live">Live</a>
      <a href="#experiments">Experiments</a>
      <a href="#review">Review</a>
      <a href="/review">Judge brief</a>
      <a href="/dashboard">Dashboard</a>
      <a href="/static/summary.html">Summary</a>
      <a href="/static/gallery_resolved.html">Resolved gallery</a>
      <a href="/static/gallery_open.html">Open gallery</a>
      <a href="/">Public page</a>
    </nav>
  </div>
</header>
<main>
  <div class="eyebrow">Private operations</div>
  <h1>Observatory</h1>
  <p class="lead">Commit, prediction store, first-call state, and review links in one place. Use this view for screenshots after the first PA call lands.</p>

  <section id="live" class="grid">
    <div class="panel span5">
      <h2>Live state</h2>
      {state_rows}
    </div>
    <div class="panel span7">
      <h2>First-call watch</h2>
      <div class="callout"><p><strong>{'PA activity observed' if prediction_count else 'Awaiting first call'}.</strong> When a call lands, inspect event shape, outcome list, total latency, parser path, warnings, and returned probabilities before changing any production routing.</p></div>
      <table style="margin-top:14px">
        <thead><tr><th>Failure mode</th><th>Status</th><th>Operator response</th></tr></thead>
        <tbody>
          <tr><td>Brave degraded</td><td class="warn">monitor via full check</td><td>Verify fallback path, do not change model first.</td></tr>
          <tr><td>Metric drift</td><td class="warn">known caveat</td><td>Keep single-binary and proper multi-class labeled separately.</td></tr>
          <tr><td>Commit drift</td><td class="ok">covered</td><td>Compare this page's commit with origin/main before deploy claims.</td></tr>
        </tbody>
      </table>
    </div>
  </section>

  <section id="experiments" class="grid">
    <div class="panel span12">
      <h2>Recent persisted predictions</h2>
      <table>
        <thead><tr><th>Market</th><th>Event</th><th>Probabilities</th><th>Latency</th><th>Parse path</th><th>Warnings</th></tr></thead>
        <tbody>{recent_rows}</tbody>
      </table>
    </div>
    <div class="panel span12">
      <h2>Experiment board</h2>
      <table>
        <thead><tr><th>Question</th><th>Status</th><th>Current answer</th><th>Next action</th></tr></thead>
        <tbody>
          <tr><td>Why not GPT-5.5?</td><td class="ok">tested</td><td>GPT-5.5 was tried. All-event single-binary Brier is <strong>0.0920</strong> vs Opus 4.7 at <strong>0.0378</strong>; its binary-only subset is strong, but that is not the full endpoint metric.</td><td>Keep as ablation, not production.</td></tr>
          <tr><td>Why current model?</td><td class="ok">measured</td><td>Best verifiable PA CLI score on current sample; Opus 4.6 is close and slightly better under proper multi-class, so this is not a settled universal claim.</td><td>Re-evaluate after live PA calls.</td></tr>
          <tr><td>Prompt ablation</td><td class="warn">in progress elsewhere</td><td>Production, no-anchor, no-scale, minimal, and meta-role variants should be tracked here once finalized.</td><td>Import final JSON, cost, and caveats.</td></tr>
          <tr><td>Retrieval count</td><td class="warn">not settled</td><td>Current count is based on prior plateau guidance, not yet independently optimized on our data.</td><td>Run 0/3/5/8 with same model and prompt.</td></tr>
          <tr><td>Scoring method</td><td class="ok">corrected</td><td>PA CLI single-binary and proper multi-class must both be labeled. Do not mix rows across metrics.</td><td>Keep public claims conservative.</td></tr>
        </tbody>
      </table>
    </div>
  </section>

  <section id="review" class="grid">
    <div class="panel span7">
      <h2>Adversarial review answers</h2>
      <ul>
        <li><strong>Do we reveal too much publicly?</strong> Yes, if the exact recipe is on `/` during active scoring. Keep method internals here.</li>
        <li><strong>Is the backtest enough?</strong> No. It is a small, binary-skewed sample. It is useful for direction and regression catching, not final proof.</li>
        <li><strong>What matters after first live call?</strong> Event schema, outcome count, total latency, warnings, and whether traces prove the pipeline behaved as expected.</li>
        <li><strong>What should not change impulsively?</strong> Production variant, model, floor formula, and Railway env vars.</li>
      </ul>
    </div>
    <div class="panel span5">
      <h2>Public surface</h2>
      <div class="kv"><span>Exact model names</span><strong>private</strong></div>
      <div class="kv"><span>Prompt/retrieval details</span><strong>private</strong></div>
      <div class="kv"><span>Raw predictions and traces</span><strong>private</strong></div>
      <div class="kv"><span>High-level system status</span><strong>public</strong></div>
      <div class="kv"><span>Health endpoint</span><strong>public</strong></div>
    </div>
  </section>
</main>
<footer>
  Internal page. Avoid screenshots that include raw traces, exact prompts, or experiment deltas during active scoring.
</footer>
</body></html>""")
    _set_dashboard_cookie_if_needed(response, request)
    return response


@app.get("/review", response_class=HTMLResponse)
def review_brief(
    request: Request,
    _: None = Depends(_require_dashboard_auth_redirect),
) -> HTMLResponse:
    """Private judge/operator briefing.

    Keep this auth-gated. It intentionally compresses evidence, caveats, and
    demo order into one page so Rob or another agent can present the system
    without exposing the full playbook on the public root.
    """
    history = _prediction_history_snapshot()
    prediction_count = len(history)
    persisted_count = _prediction_store_count()
    first_call_status = (
        "PA activity observed" if prediction_count or persisted_count else "waiting for first Prophet Arena call"
    )
    response = HTMLResponse(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForecastingPath · Judge review brief</title>
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<style>
  :root {{
    --bg: #f7f8fb; --panel: #ffffff; --line: #d8dde6; --ink: #111827;
    --muted: #667085; --accent: #1d4ed8; --ok: #047857; --warn: #b45309;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink);
          font: 15px/1.54 -apple-system, "Segoe UI", system-ui, sans-serif; }}
  main {{ max-width: 1180px; margin: 0 auto; padding: 28px 22px 56px; }}
  header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 28px; }}
  h1 {{ margin: 0; font-size: clamp(2.2rem, 5vw, 4.8rem); line-height: 0.96; letter-spacing: 0; }}
  h2 {{ margin: 0 0 12px; font-size: 0.96rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.07em; }}
  p {{ color: var(--muted); margin: 0.5rem 0; }}
  a {{ color: var(--accent); text-decoration: none; font-weight: 650; }}
  a:hover {{ text-decoration: underline; }}
  .nav {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 12px; min-width: 240px; }}
  .lead {{ max-width: 720px; font-size: 1.05rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; margin-top: 16px; }}
  .section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; }}
  .span4 {{ grid-column: span 4; }} .span5 {{ grid-column: span 5; }} .span7 {{ grid-column: span 7; }} .span12 {{ grid-column: span 12; }}
  .proof {{ display: grid; gap: 9px; }}
  .kv {{ display: flex; justify-content: space-between; gap: 16px; padding-top: 9px; border-top: 1px solid #edf1f7; }}
  .kv:first-child {{ border-top: 0; padding-top: 0; }}
  .kv span {{ color: var(--muted); }} .kv strong {{ text-align: right; overflow-wrap: anywhere; }}
  ol, ul {{ margin: 0; padding-left: 1.1rem; }}
  li {{ margin: 0.45rem 0; color: #374151; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; vertical-align: top; border-bottom: 1px solid #edf1f7; padding: 10px 8px; }}
  th {{ color: var(--muted); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.06em; }}
  tr:last-child td {{ border-bottom: 0; }}
  code {{ background: #eef2f7; border: 1px solid #dbe3ef; padding: 1px 5px; border-radius: 4px; }}
  .ok {{ color: var(--ok); }} .warn {{ color: var(--warn); }}
  @media (max-width: 820px) {{
    header {{ display: block; }}
    .nav {{ justify-content: flex-start; margin-top: 16px; }}
    .span4, .span5, .span7, .span12 {{ grid-column: 1 / -1; }}
  }}
</style>
</head><body>
<main>
  <header>
    <div>
      <h1>Judge review brief</h1>
      <p class="lead">Operator one-pager. Live system state, the evidence the agent is using, current caveats, and the first-PA-call checklist on a single screen.</p>
    </div>
    <nav class="nav">
      <a href="/dashboard">Dashboard</a>
      <a href="/observatory">Observatory</a>
      <a href="/static/summary.html">Summary</a>
      <a href="/static/gallery_resolved.html">Resolved gallery</a>
      <a href="/static/gallery_open.html">Open gallery</a>
    </nav>
  </header>

  <section class="grid">
    <div class="section span4">
      <h2>Current proof</h2>
      <div class="proof">
        <div class="kv"><span>Live commit</span><strong>{html_escape(_BUILD_COMMIT_SHA)}</strong></div>
        <div class="kv"><span>Variant</span><strong>{html_escape(_VARIANT_NAME)}</strong></div>
        <div class="kv"><span>Prediction records</span><strong>{prediction_count} memory / {persisted_count} disk</strong></div>
        <div class="kv"><span>PA status</span><strong>{html_escape(first_call_status)}</strong></div>
        <div class="kv"><span>Public posture</span><strong>internals private</strong></div>
      </div>
    </div>
    <div class="section span4">
      <h2>Demo script</h2>
      <ol>
        <li>Open <a href="/">public root</a>; show that it is sparse and does not expose ablations.</li>
        <li>Open <a href="/dashboard">dashboard</a>; show commit, variant, first-call status, and private links.</li>
        <li>Run the pipeline demo and point to stage updates plus returned JSON.</li>
        <li>Open <a href="/static/summary.html">summary</a> for the scored evidence and metric caveats.</li>
        <li>Open galleries to show per-event behavior instead of only aggregate claims.</li>
      </ol>
    </div>
    <div class="section span4">
      <h2>First Prophet Arena call</h2>
      <ul>
        <li>Check <code>/predictions</code> count and disk persistence.</li>
        <li>Inspect outcome count, parser path, warnings, and total latency.</li>
        <li>Compare event shape with the assumptions in the dashboard demo.</li>
        <li>Do not change the production variant without measured evidence.</li>
      </ul>
    </div>
  </section>

  <section class="grid">
    <div class="section span12">
      <h2>Likely questions</h2>
      <table>
        <thead><tr><th>Question</th><th>Answer to give</th><th>Evidence link</th></tr></thead>
        <tbody>
          <tr><td>Why not GPT-5.5?</td><td>It was tested. The binary subset looked strong, but full endpoint behavior and schema compliance matter; the current report keeps it as an ablation rather than production.</td><td><a href="/static/summary.html">summary report</a></td></tr>
          <tr><td>Is the backtest enough?</td><td>No. It is small and partially vulnerable to resolved-event retrieval leakage. We treat it as regression evidence, not final proof of live performance.</td><td><a href="/compare">comparison grid</a></td></tr>
          <tr><td>What makes the system autonomous?</td><td>The endpoint accepts event JSON, retrieves evidence, produces structured probabilities, logs traces, streams operator state, and persists prediction records without manual scoring work.</td><td><a href="/dashboard">dashboard</a></td></tr>
          <tr><td>What is the biggest current risk?</td><td>First live PA payload shape and scoring behavior are still the decisive unknowns. The first-call checklist exists to inspect that before changing model or prompt.</td><td><a href="/observatory">observatory</a></td></tr>
          <tr><td>Do we reveal too much publicly?</td><td>The public root is sparse. Exact model names, ablations, galleries, traces, and research reports are dashboard-auth gated.</td><td><a href="/">public root</a></td></tr>
        </tbody>
      </table>
    </div>
  </section>
</main>
</body></html>""")
    _set_dashboard_cookie_if_needed(response, request)
    return response


# Rate-limit PIN entry to slow brute-force. Per-IP, in-memory; resets on
# process restart, which is fine for hackathon scope. Production-grade
# version would use Redis or similar.
_PIN_ATTEMPTS: dict[str, list[float]] = {}
_PIN_MAX_PER_MINUTE = 6
_PIN_LOCKOUT_AFTER = 12  # after this many failed attempts, ignore for 5 min
_PIN_LOCKOUT_SECONDS = 300


def _pin_rate_limit_ok(ip: str) -> tuple[bool, str]:
    now = time.time()
    history = _PIN_ATTEMPTS.get(ip, [])
    # Drop entries older than 5 minutes
    history = [t for t in history if now - t < _PIN_LOCKOUT_SECONDS]
    _PIN_ATTEMPTS[ip] = history
    if len(history) >= _PIN_LOCKOUT_AFTER:
        return False, "Too many failed attempts. Try again in a few minutes."
    recent = [t for t in history if now - t < 60]
    if len(recent) >= _PIN_MAX_PER_MINUTE:
        return False, "Slow down. Wait a minute before trying again."
    return True, ""


def _record_pin_attempt(ip: str) -> None:
    _PIN_ATTEMPTS.setdefault(ip, []).append(time.time())


def _login_page_html(*, next_path: str, error: str = "") -> str:
    """The PIN entry form. Same visual language as the public landing."""
    from html import escape as _esc
    next_safe = _esc(next_path) if _is_safe_redirect(next_path) else "/dashboard"
    err_html = (f'<p class="err">{_esc(error)}</p>' if error else "")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForecastingPath · sign in</title>
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #f7f8fb; color: #111827;
         font: 16px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif; }}
  main {{ width: min(420px, calc(100vw - 32px)); background: #ffffff;
         border: 1px solid #d8dde6; border-radius: 10px; padding: 28px 32px;
         box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  .brand {{ display: flex; align-items: center; gap: 0.6em; margin: 0 0 1em; }}
  .brand img {{ width: 36px; height: 36px; border-radius: 8px; }}
  .brand b {{ font-size: 1.05em; letter-spacing: 0.01em; }}
  h1 {{ margin: 0 0 0.3em; font-size: 1.25em; }}
  p {{ color: #5b6472; margin: 0.5em 0; font-size: 0.94em; }}
  .err {{ color: #b91c1c; font-weight: 600; }}
  form {{ display: grid; gap: 0.7em; margin-top: 1em; }}
  input[type="password"], input[type="text"] {{
    width: 100%; padding: 0.7em 0.8em; font: inherit; font-size: 1.05em;
    border: 1px solid #d8dde6; border-radius: 6px; letter-spacing: 0.2em;
    text-align: center;
  }}
  button {{ width: 100%; padding: 0.75em; font: inherit; font-size: 1em;
           font-weight: 650; background: #1d4ed8; color: white;
           border: 0; border-radius: 6px; cursor: pointer; }}
  button:hover {{ background: #1e40af; }}
  .foot {{ font-size: 0.84em; text-align: center; margin-top: 1em; }}
  a {{ color: #1d4ed8; text-decoration: none; font-weight: 650; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head><body>
<main>
  <div class="brand">
    <img src="/static/flaviconlogo.webp" alt="ForecastingPath">
    <b>ForecastingPath</b>
  </div>
  <h1>Sign in to view the dashboard</h1>
  <p>Operator console (PIN-gated). The public submission report is
     available without sign-in at <a href="/static/summary.pdf">/static/summary.pdf</a>.</p>
  {err_html}
  <form method="post" action="/login" autocomplete="off">
    <input name="pin" type="password" inputmode="numeric" autocomplete="off"
           placeholder="PIN" autofocus required>
    <input type="hidden" name="next" value="{next_safe}">
    <button type="submit">Enter</button>
  </form>
  <p class="foot"><a href="/">← back to ForecastingPath</a></p>
</main>
</body></html>"""


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_get(request: Request) -> HTMLResponse:
    if _is_dashboard_authorized(request):
        # Already logged in; bounce to wherever they were going.
        next_path = request.query_params.get("next", "/dashboard")
        if _is_safe_redirect(next_path):
            return RedirectResponse(next_path, status_code=303)  # type: ignore[return-value]
        return RedirectResponse("/dashboard", status_code=303)  # type: ignore[return-value]
    next_path = request.query_params.get("next", "/dashboard")
    error = request.query_params.get("error", "")
    return HTMLResponse(_login_page_html(next_path=next_path, error=error))


@app.post("/login", include_in_schema=False)
async def login_post(request: Request) -> Response:
    form = await request.form()
    pin = str(form.get("pin", "")).strip()
    next_path = str(form.get("next", "/dashboard"))
    ip = (request.client.host if request.client else "unknown")
    expected_pin = _dashboard_pin()
    token = _dashboard_auth_token()

    if not expected_pin or not token:
        # Misconfigured server; fall back to a friendly error rather than
        # a 500.
        return HTMLResponse(
            _login_page_html(next_path=next_path,
                             error="Server not configured for PIN auth. Try the ?token=… URL.")
        )

    ok, msg = _pin_rate_limit_ok(ip)
    if not ok:
        return HTMLResponse(_login_page_html(next_path=next_path, error=msg))

    if not secrets.compare_digest(pin, expected_pin):
        _record_pin_attempt(ip)
        return HTMLResponse(_login_page_html(next_path=next_path, error="Wrong PIN. Try again."))

    # Success: set the auth-token cookie + redirect.
    redirect_to = next_path if _is_safe_redirect(next_path) else "/dashboard"
    response = RedirectResponse(redirect_to, status_code=303)
    response.set_cookie(
        "dashboard_token", token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@app.post("/logout", include_in_schema=False)
def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("dashboard_token")
    return response


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Serve the real favicon. Falls back to 204 if static/ wasn't bundled
    (which would be a deploy bug -- preflight checks for it now)."""
    ico = _STATIC_DIR / "favicon.ico"
    if ico.exists():
        return Response(content=ico.read_bytes(), media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=204)


@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def apple_touch_icon() -> Response:
    """iOS expects this at the root path. Serve the 192px PNG we already have."""
    png = _STATIC_DIR / "icon-192.png"
    if png.exists():
        return Response(content=png.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)


@app.get("/robots.txt", include_in_schema=False)
def robots() -> Response:
    """Standard search-engine permissions. We're a forecasting endpoint, not
    a content site, but having robots.txt at root is basic web hygiene and
    keeps log noise down."""
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /predict\n"
        "Disallow: /predictions\n"
        "Disallow: /dashboard\n"
        "Disallow: /observatory\n"
        "Disallow: /demo/\n"
        "\n"
        "Sitemap: https://forecastingpath.com/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> Response:
    """Minimal sitemap covering the public-facing URLs an external indexer
    could reasonably crawl. Auth-gated pages are excluded."""
    urls = [
        "https://forecastingpath.com/",
        "https://forecastingpath.com/healthz",
        "https://forecastingpath.com/static/summary.pdf",
        "https://forecastingpath.com/static/architecture.svg",
        "https://forecastingpath.com/llms.txt",
    ]
    items = "\n".join(f"  <url><loc>{u}</loc></url>" for u in urls)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{items}\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/llms.txt", include_in_schema=False)
def llms_txt() -> Response:
    """Emerging convention for AI agents discovering a site. Tells a
    language-model crawler what this site is and where the structured
    artifacts are. On-brand for a forecasting agent serving an AI
    benchmark."""
    body = (
        "# ForecastingPath\n"
        "\n"
        "> The Oracles: a calibrated retrieval-augmented forecasting agent\n"
        "> built for Prophet Hacks 2026. Team CanadaHacks, forecasting track.\n"
        "\n"
        "## What this domain serves\n"
        "\n"
        "- `/predict` (POST): live forecasting endpoint. Accepts a Prophet\n"
        "  Arena event payload, returns per-outcome probabilities.\n"
        "- `/healthz` (GET): liveness + the currently deployed commit SHA.\n"
        "- `/v1/chat/completions` (POST): OpenAI-compatible shim for the\n"
        "  PA onboarding form. Bearer-auth required.\n"
        "- `/` (GET): a small public landing page.\n"
        "\n"
        "## Where the structured information lives\n"
        "\n"
        "- Source code: https://github.com/Robby955/prophet-hacks\n"
        "- Submission report: https://forecastingpath.com/static/summary.pdf\n"
        "- Architecture diagram: https://forecastingpath.com/static/architecture.svg\n"
        "- Live commit SHA: https://forecastingpath.com/healthz\n"
        "\n"
        "## Pages not intended for crawling\n"
        "\n"
        "- `/dashboard`, `/observatory`, `/compare`, `/predictions`,\n"
        "  `/demo/*`: PIN-gated operator views. Not public content.\n"
    )
    return Response(content=body, media_type="text/plain; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600"})


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

    evidence_urls = result.get("evidence_urls") or []
    if not isinstance(evidence_urls, list):
        evidence_urls = []
    # Pipeline trace from the variant. Stored in /predictions for inspection
    # but explicitly NOT returned to Prophet Arena (their schema is just
    # `probabilities`). Surfaced on /dashboard for debugging.
    trace = result.get("_trace") if isinstance(result.get("_trace"), dict) else None
    prediction_record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_ticker": event.event_ticker,
        "market_ticker": event.market_ticker,
        "title": event.title,
        "category": event.category,
        "close_time": event.close_time,
        "p_yes": p_yes,
        "outcomes": outcomes,
        "probabilities": probs,
        "rationale": rationale,
        "evidence_urls": evidence_urls[:8],
        "variant": _VARIANT_NAME,
        "commit": _BUILD_COMMIT_SHA,
        "trace": trace,
    }
    _PREDICTION_HISTORY.appendleft(prediction_record)
    _append_prediction_record(prediction_record)
    # Live KPIs for the dashboard.
    global _TOTAL_PREDICTIONS, _TOTAL_COST_USD, _PPM_CURRENT_MINUTE, _PPM_CURRENT_COUNT
    _TOTAL_PREDICTIONS += 1
    _TOTAL_COST_USD += _VARIANT_COSTS.get(_VARIANT_NAME, 0.0)

    # Per-minute bucket for sparkline. When the minute rolls over, flush
    # the previous count into the history deque.
    now_min = int(time.time() // 60)
    if _PPM_CURRENT_MINUTE != now_min:
        if _PPM_CURRENT_MINUTE != -1:
            _PREDICTIONS_PER_MIN.append(_PPM_CURRENT_COUNT)
        _PPM_CURRENT_MINUTE = now_min
        _PPM_CURRENT_COUNT = 0
    _PPM_CURRENT_COUNT += 1

    # Fan out to SSE subscribers so the dashboard can flash + prepend.
    sse_msg = {
        "type": "prediction",
        "ts": datetime.now(timezone.utc).isoformat(),
        "market_ticker": event.market_ticker,
        "title": event.title[:200],
        "category": event.category,
        "p_yes": p_yes,
        "probabilities": probs,
        "rationale": rationale[:240],
        "evidence_urls": evidence_urls[:5],
        "total_predictions": _TOTAL_PREDICTIONS,
        "total_cost_usd": round(_TOTAL_COST_USD, 4),
    }
    for q in list(_SSE_SUBSCRIBERS):
        try:
            q.put_nowait(sse_msg)
        except Exception:
            pass
    return PredictionResponse(
        probabilities=[OutcomeProbability(**p) for p in probs],
        rationale=rationale,
    )


@app.get("/predictions")
def predictions(_: None = Depends(_require_dashboard_auth)) -> dict[str, Any]:
    """Last 50 predictions served. Machine-readable."""
    history = _prediction_history_snapshot()
    return {
        "count": len(history),
        "persisted_count": _prediction_store_count(),
        "predictions": history,
    }


def _demo_event_payload() -> dict[str, Any]:
    return {
        "event_ticker": "dashboard-demo-fed-2026",
        "market_ticker": "dashboard-demo-fed-2026",
        "title": "Will the US Federal Reserve cut rates at the December 2026 meeting?",
        "category": "Economics",
        "close_time": "2026-12-31T23:59:59Z",
        "outcomes": ["Yes", "No"],
        "description": "Dashboard demo event for inspecting the production forecasting pipeline.",
        "rules": "YES if the FOMC announces a rate cut at the December 2026 meeting; otherwise NO.",
    }


def _prune_demo_runs() -> None:
    if len(_DEMO_RUNS) <= _DEMO_MAX_RUNS:
        return
    ordered = sorted(
        _DEMO_RUNS.items(),
        key=lambda item: str(item[1].get("created_at", "")),
    )
    for run_id, _run in ordered[: max(0, len(ordered) - _DEMO_MAX_RUNS)]:
        _DEMO_RUNS.pop(run_id, None)


def _record_demo_event(
    run_id: str,
    stage: str,
    status_text: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> None:
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "status": status_text,
        "message": message,
    }
    if extra:
        event["extra"] = extra
    with _DEMO_RUN_LOCK:
        run = _DEMO_RUNS.get(run_id)
        if run is None:
            return
        run["events"].append(event)
        run["updated_at"] = event["ts"]


def _finish_demo_run(run_id: str, result: dict[str, Any]) -> None:
    probs = result.get("probabilities") or []
    if not isinstance(probs, list):
        probs = []
    summary = {
        "p_yes": float(result.get("p_yes", 0.5)),
        "rationale": str(result.get("rationale", ""))[:300],
        "probabilities": probs,
        "evidence_urls": (result.get("evidence_urls") or [])[:8],
        "trace": result.get("_trace") if isinstance(result.get("_trace"), dict) else None,
    }
    with _DEMO_RUN_LOCK:
        run = _DEMO_RUNS.get(run_id)
        if run is None:
            return
        run["events"].append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "stage": "completed",
                "status": "completed",
                "message": "result ready",
                "extra": {"p_yes": summary["p_yes"]},
            }
        )
        run["status"] = "completed"
        run["result"] = summary


def _fail_demo_run(run_id: str, error: Exception) -> None:
    with _DEMO_RUN_LOCK:
        run = _DEMO_RUNS.get(run_id)
        if run is not None:
            run["events"].append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "stage": "failed",
                    "status": "failed",
                    "message": str(error)[:240],
                }
            )
            run["status"] = "failed"
            run["error"] = str(error)[:500]


def _run_demo_pipeline(run_id: str) -> None:
    event = _demo_event_payload()
    try:
        _record_demo_event(
            run_id,
            "build_event",
            "running",
            "synthetic event ready",
            {"market_ticker": event["market_ticker"], "outcomes": event["outcomes"]},
        )
        _record_demo_event(
            run_id,
            "forecast",
            "running",
            f"calling {_VARIANT_NAME}",
        )
        started = time.monotonic()
        result = _VARIANT_FN(event)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        trace = result.get("_trace") if isinstance(result.get("_trace"), dict) else {}
        _record_demo_event(
            run_id,
            "forecast",
            "completed",
            "forecast returned",
            {
                "latency_ms": elapsed_ms,
                "trace_latency_ms": trace.get("latency_ms") if isinstance(trace, dict) else None,
            },
        )
        _finish_demo_run(run_id, result)
    except Exception as exc:
        log.exception("demo run %s failed", run_id)
        _fail_demo_run(run_id, exc)


@app.post("/demo/start")
def demo_start(_: None = Depends(_require_dashboard_auth)) -> dict[str, str]:
    """Start a PIN-protected synthetic run through the real forecast variant."""
    run_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with _DEMO_RUN_LOCK:
        active_runs = sum(
            1 for run in _DEMO_RUNS.values() if run.get("status") == "running"
        )
        if active_runs >= _DEMO_MAX_ACTIVE_RUNS:
            raise HTTPException(status_code=429, detail="too many active demo runs")
        _DEMO_RUNS[run_id] = {
            "run_id": run_id,
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "events": [],
            "result": None,
            "error": None,
        }
        _prune_demo_runs()
    _record_demo_event(run_id, "queued", "running", "demo queued")
    threading.Thread(
        target=_run_demo_pipeline,
        args=(run_id,),
        daemon=True,
        name=f"forecast-demo-{run_id}",
    ).start()
    return {
        "run_id": run_id,
        "stream_url": f"/demo/stream/{run_id}",
        "result_url": f"/demo/result/{run_id}",
    }


@app.get("/demo/result/{run_id}")
def demo_result(
    run_id: str,
    _: None = Depends(_require_dashboard_auth),
) -> dict[str, Any]:
    with _DEMO_RUN_LOCK:
        run = _DEMO_RUNS.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="demo run not found")
        return json.loads(json.dumps(run, default=str))


@app.get("/demo/stream/{run_id}")
async def demo_stream(
    run_id: str,
    _: None = Depends(_require_dashboard_auth),
) -> StreamingResponse:
    with _DEMO_RUN_LOCK:
        if run_id not in _DEMO_RUNS:
            raise HTTPException(status_code=404, detail="demo run not found")

    async def gen():
        idx = 0
        while True:
            with _DEMO_RUN_LOCK:
                run = _DEMO_RUNS.get(run_id)
                if run is None:
                    break
                events = list(run.get("events", []))
                status_text = str(run.get("status") or "running")
            for event in events[idx:]:
                yield "event: demo\ndata: " + json.dumps(event) + "\n\n"
            idx = len(events)
            if status_text in {"completed", "failed"}:
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- /compare --------------------------------------------------------------
# Multi-model multi-event comparison page. Built 2026-05-16 to give the
# evaluator/showcase view Rob asked for: "browse all these visually,
# see how or what my model said or what different models said if they
# took different routes in the pipeline, measure and view things."


_ABLATION_FILES = {
    # label -> filename (under data/predictions/)
    "Opus 4.7 (production)": "multi_outcome_retrieval.json",
    "Sonnet 4.6 (prev prod)": "multi_outcome_retrieval.phase1_sonnet.json",
    "Opus 4.6": "ablation_claude-opus-4-6.json",
    "GPT-5.2": "ablation_gpt-5-2.json",
    "Gemini 3.1 Pro (post-harden)": "ablation_gemini-3-1-pro-preview-postharden.json",
}


def _load_compare_data() -> dict[str, Any]:
    """Merge all ablation prediction files + resolved.json ground truth
    into a per-event-per-model view."""
    base = Path(__file__).resolve().parent
    pred_dir = base / "data/predictions"

    # Ground truth from resolved.json
    resolved_path = base / "data/resolved.json"
    if not resolved_path.exists():
        return {"events": [], "models": list(_ABLATION_FILES.keys()), "summary": {}}
    resolved = json.loads(resolved_path.read_text())
    by_ticker: dict[str, dict[str, Any]] = {e["market_ticker"]: e for e in resolved}

    # Per-model predictions (market_ticker -> p_yes + probabilities)
    model_preds: dict[str, dict[str, dict[str, Any]]] = {}
    for label, fname in _ABLATION_FILES.items():
        p = pred_dir / fname
        if not p.exists():
            model_preds[label] = {}
            continue
        data = json.loads(p.read_text())
        preds = data.get("predictions") if isinstance(data, dict) else data
        if not isinstance(preds, list):
            model_preds[label] = {}
            continue
        model_preds[label] = {x.get("market_ticker"): x for x in preds if x.get("market_ticker")}

    # Build per-event rows
    rows = []
    summary_briers: dict[str, list[float]] = {label: [] for label in _ABLATION_FILES}
    for ticker, ev in by_ticker.items():
        outs = ev.get("outcomes") or []
        ro = ev.get("resolved_outcome") or {}
        winner_list = ro.get("value") if isinstance(ro, dict) else None
        winner = winner_list[0] if winner_list else None
        if not outs or winner not in outs:
            continue
        winner_idx = outs.index(winner)
        actual_binary = 1 if winner_idx == 0 else 0

        per_model: dict[str, Any] = {}
        for label in _ABLATION_FILES:
            pr = model_preds.get(label, {}).get(ticker)
            if not pr:
                per_model[label] = {"p_yes": None, "brier": None, "rationale": "", "probs": []}
                continue
            p_yes = pr.get("p_yes")
            probs = pr.get("probabilities") or []
            if len(outs) == 2 and p_yes is not None:
                b = (p_yes - actual_binary) ** 2
            elif probs:
                # multi-outcome Brier
                prob_map = {p.get("market"): p.get("probability", 0.0) for p in probs}
                b = sum(
                    (prob_map.get(o, 1.0 / len(outs)) - (1.0 if i == winner_idx else 0.0)) ** 2
                    for i, o in enumerate(outs)
                )
            else:
                b = None
            per_model[label] = {
                "p_yes": p_yes, "brier": b,
                "rationale": (pr.get("rationale") or "")[:240],
                "probs": probs,
            }
            if b is not None:
                summary_briers[label].append(b)

        rows.append({
            "ticker": ticker,
            "title": ev.get("title", ""),
            "category": ev.get("category", "?"),
            "n_outcomes": len(outs),
            "winner": winner,
            "outcomes": outs,
            "models": per_model,
        })

    # Sort by max delta between models to show interesting events first
    def _delta(r: dict) -> float:
        bs = [m["brier"] for m in r["models"].values() if m["brier"] is not None]
        return (max(bs) - min(bs)) if len(bs) >= 2 else 0.0
    rows.sort(key=_delta, reverse=True)

    summary = {
        label: {
            "mean_brier": (sum(bs) / len(bs)) if bs else None,
            "n": len(bs),
        }
        for label, bs in summary_briers.items()
    }
    return {"events": rows, "models": list(_ABLATION_FILES.keys()), "summary": summary}


def _brier_color(b: float | None) -> str:
    """Map a Brier value to a CSS color (green=good, red=bad)."""
    if b is None:
        return "#e5e7eb"
    # Brier on binary is in [0, 1]; multi-outcome can exceed 1.
    # Bin: <0.05 deep green, <0.10 green, <0.25 yellow, <0.50 orange, else red.
    if b < 0.05: return "#86efac"  # green-300
    if b < 0.10: return "#bef264"  # lime-300
    if b < 0.25: return "#fde68a"  # amber-200
    if b < 0.50: return "#fdba74"  # orange-300
    return "#fca5a5"  # red-300


def _compare_reliability_svg(data: dict[str, Any]) -> str:
    """Render reliability for p(outcome[0]) on resolved comparison data."""
    models = data.get("models") or []
    if not models:
        return "<div class='reliability-panel'><h2>Reliability diagram</h2><p class='meta'>No model data available.</p></div>"
    model_label = next((m for m in models if "production" in m.lower()), models[0])

    probs: list[float] = []
    outcomes: list[int] = []
    for row in data.get("events", []):
        outs = row.get("outcomes") or []
        if not outs:
            continue
        pred = (row.get("models") or {}).get(model_label) or {}
        p_yes = pred.get("p_yes")
        if p_yes is None:
            continue
        probs.append(max(0.0, min(1.0, float(p_yes))))
        outcomes.append(1 if row.get("winner") == outs[0] else 0)

    if not probs:
        return (
            "<div class='reliability-panel'><h2>Reliability diagram</h2>"
            f"<p class='meta'>No binary p(outcome[0]) rows for {html_escape(model_label)}.</p></div>"
        )

    bins = reliability_diagram_data(probs, outcomes, n_bins=10)
    ece = expected_calibration_error(probs, outcomes, n_bins=10)
    width, height = 520, 280
    pad_l, pad_r, pad_t, pad_b = 54, 24, 22, 44
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def x(p: float) -> float:
        return pad_l + p * plot_w

    def y(p: float) -> float:
        return pad_t + (1.0 - p) * plot_h

    grid = []
    for i in range(0, 11, 2):
        v = i / 10
        grid.append(
            f"<line x1='{x(v):.1f}' y1='{pad_t}' x2='{x(v):.1f}' y2='{height - pad_b}' class='grid-line'/>"
            f"<line x1='{pad_l}' y1='{y(v):.1f}' x2='{width - pad_r}' y2='{y(v):.1f}' class='grid-line'/>"
        )
    points = [
        f"{x(b.p_mean):.1f},{y(b.outcome_mean):.1f}"
        for b in bins
        if b.count > 0
    ]
    circles = []
    for b in bins:
        if b.count == 0:
            continue
        r = min(12, 4 + b.count * 1.5)
        circles.append(
            f"<circle cx='{x(b.p_mean):.1f}' cy='{y(b.outcome_mean):.1f}' r='{r:.1f}' class='rel-point'>"
            f"<title>forecast {b.p_mean:.2f}, observed {b.outcome_mean:.2f}, n={b.count}</title>"
            "</circle>"
        )
    polyline = (
        f"<polyline points='{' '.join(points)}' class='rel-line'/>"
        if len(points) >= 2 else ""
    )
    count = len(probs)
    return f"""
<section class='reliability-panel'>
  <div>
    <h2>Reliability diagram</h2>
    <p class='meta'>Production model calibration on resolved rows: <strong>{html_escape(model_label)}</strong>. Small-n view: {count} outcome[0] probabilities, ECE {ece:.3f}.</p>
  </div>
  <svg class='reliability-chart' width='{width}' height='{height}' viewBox='0 0 {width} {height}' role='img' aria-label='Reliability diagram for {html_escape(model_label)}'>
    {''.join(grid)}
    <line x1='{pad_l}' y1='{height - pad_b}' x2='{width - pad_r}' y2='{height - pad_b}' class='axis-line'/>
    <line x1='{pad_l}' y1='{pad_t}' x2='{pad_l}' y2='{height - pad_b}' class='axis-line'/>
    <line x1='{x(0):.1f}' y1='{y(0):.1f}' x2='{x(1):.1f}' y2='{y(1):.1f}' class='perfect-line'/>
    {polyline}
    {''.join(circles)}
    <text x='{width / 2:.1f}' y='{height - 9}' class='axis-label'>Mean forecast probability</text>
    <text x='16' y='{height / 2:.1f}' class='axis-label rotate'>Observed frequency</text>
    <text x='{x(0.58):.1f}' y='{y(0.66):.1f}' class='perfect-label'>Perfect calibration</text>
  </svg>
</section>"""


@app.get("/compare", response_class=HTMLResponse)
def compare(
    request: Request,
    _: None = Depends(_require_dashboard_auth_redirect),
) -> HTMLResponse:
    """Multi-model multi-event comparison grid for the resolved backtest set."""
    data = _load_compare_data()
    models = data["models"]
    events = data["events"]
    summary = data["summary"]

    # Summary header
    summary_cells = []
    for label in models:
        s = summary.get(label, {})
        mb = s.get("mean_brier")
        mb_str = f"{mb:.4f}" if mb is not None else "—"
        color = _brier_color(mb)
        summary_cells.append(
            f"<div class='sum-cell' style='border-left:6px solid {color}'>"
            f"<div class='sum-label'>{html_escape(label)}</div>"
            f"<div class='sum-brier'>{mb_str}</div>"
            f"<div class='sum-n'>n={s.get('n', 0)} resolved</div></div>"
        )

    # Per-event rows
    header_cells = "".join(f"<th>{html_escape(m)}</th>" for m in models)
    body_rows = []
    for r in events:
        cells = []
        cells.append(f"<td class='cat-cell'>{html_escape(r['category'])}</td>")
        cells.append(
            f"<td class='title-cell'><strong>{html_escape(r['title'][:90])}</strong>"
            f"<div class='muted small'>n={r['n_outcomes']} winner: {html_escape(r['winner'][:40])}</div></td>"
        )
        for label in models:
            m = r["models"][label]
            b = m["brier"]
            color = _brier_color(b)
            p_yes = m["p_yes"]
            p_str = f"{p_yes:.2f}" if p_yes is not None else "—"
            b_str = f"{b:.3f}" if b is not None else "—"
            tooltip = html_escape((m.get("rationale") or "")[:200])
            cells.append(
                f"<td class='b-cell' style='background:{color}' title='{tooltip}'>"
                f"<div class='b-prob'>p={p_str}</div>"
                f"<div class='b-brier'>brier {b_str}</div></td>"
            )
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    rows_html = "\n".join(body_rows)
    n_events = len(events)
    summary_html = "\n".join(summary_cells)
    reliability_html = _compare_reliability_svg(data)

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForecastingPath · Model comparison</title>
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<style>
  :root {{
    --bg: #f7f8fb; --panel: #ffffff; --border: #d8dde6;
    --text: #111827; --muted: #5b6472; --accent: #1d4ed8;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font: 14px/1.45 -apple-system, "Segoe UI", system-ui, sans-serif; }}
  .page {{ max-width: 1400px; margin: 0 auto; padding: 1.5em 1em 3em; }}
  h1 {{ margin: 0 0 0.4em; font-size: 1.7rem; }}
  .meta {{ color: var(--muted); font-size: 0.92em; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                   gap: 0.8em; margin: 1.2em 0; }}
  .sum-cell {{ background: var(--panel); border: 1px solid var(--border);
               border-radius: 8px; padding: 0.9em 1em; }}
  .sum-label {{ font-size: 0.84em; color: var(--muted); font-weight: 700;
                text-transform: uppercase; letter-spacing: 0.04em; }}
  .sum-brier {{ font-size: 1.6em; font-weight: 700; margin: 0.1em 0; }}
  .sum-n {{ font-size: 0.82em; color: var(--muted); }}
  table {{ width: 100%; border-collapse: separate; border-spacing: 0;
           background: var(--panel); border: 1px solid var(--border);
           border-radius: 8px; overflow: hidden; }}
  th, td {{ padding: 0.55em 0.7em; text-align: left;
            border-bottom: 1px solid var(--border); vertical-align: top; }}
  thead th {{ background: #f1f3f7; position: sticky; top: 0; z-index: 2;
              font-weight: 700; font-size: 0.84em; text-transform: uppercase;
              letter-spacing: 0.04em; color: var(--muted); }}
  .title-cell {{ min-width: 260px; max-width: 360px; }}
  .cat-cell {{ font-weight: 700; color: var(--accent); white-space: nowrap; }}
  .b-cell {{ text-align: center; min-width: 90px; }}
  .b-prob {{ font-weight: 700; font-size: 1.02em; }}
  .b-brier {{ font-size: 0.78em; color: #374151; opacity: 0.85; }}
  .muted {{ color: var(--muted); }}
  .small {{ font-size: 0.84em; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 0.6em; margin: 0.7em 0; font-size: 0.85em; }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 0.35em; }}
  .legend-swatch {{ width: 14px; height: 14px; border-radius: 3px; border: 1px solid #cbd5e1; }}
  .reliability-panel {{ margin: 1.2em 0; background: var(--panel); border: 1px solid var(--border);
                        border-radius: 8px; padding: 1em; display: grid;
                        grid-template-columns: minmax(220px, 0.8fr) minmax(320px, 1.2fr);
                        gap: 1em; align-items: center; }}
  .reliability-panel h2 {{ margin: 0 0 0.3em; font-size: 1.05rem; }}
  .reliability-chart {{ width: 100%; max-width: 520px; height: auto; }}
  .grid-line {{ stroke: #e5e7eb; stroke-width: 1; }}
  .axis-line {{ stroke: #64748b; stroke-width: 1.2; }}
  .perfect-line {{ stroke: #64748b; stroke-width: 1.2; stroke-dasharray: 5 5; }}
  .rel-line {{ fill: none; stroke: var(--accent); stroke-width: 2.5; }}
  .rel-point {{ fill: #1d4ed8; fill-opacity: 0.78; stroke: #ffffff; stroke-width: 1.5; }}
  .axis-label, .perfect-label {{ fill: var(--muted); font-size: 12px; }}
  .rotate {{ transform: rotate(-90deg); transform-origin: 16px 140px; }}
  @media (max-width: 760px) {{ .reliability-panel {{ grid-template-columns: 1fr; }} }}
  a {{ color: var(--accent); text-decoration: none; font-weight: 650; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head><body>
<div class="page">

<h1>Model comparison — 26-event sample-resolved set</h1>
<p class="meta">
Same pipeline (Brave retrieval + market-anchor prompt + 0.10 longshot floor), five different LLMs.
Each cell shows <code>p</code> = probability assigned to <em>outcome[0]</em> and <code>brier</code> = Brier loss for the resolved outcome.
Hover any cell for the model's rationale on that event.
Rows sorted by inter-model spread — most contested events first.
Lower Brier = better. Random binary baseline = 0.25, uniform-prior baseline = 0.22.
</p>

<div class="legend">
  <span class="legend-item"><span class="legend-swatch" style="background:#86efac"></span> &lt; 0.05 (excellent)</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#bef264"></span> &lt; 0.10</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#fde68a"></span> &lt; 0.25</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#fdba74"></span> &lt; 0.50</span>
  <span class="legend-item"><span class="legend-swatch" style="background:#fca5a5"></span> ≥ 0.50 (catastrophic)</span>
</div>

<div class="summary-grid">
{summary_html}
</div>

{reliability_html}

<p class="meta">{n_events} events × {len(models)} models = {n_events * len(models)} predictions in the grid below. Tooltips show rationale.</p>

<table>
<thead><tr><th>Category</th><th>Event (outcomes; resolved winner)</th>{header_cells}</tr></thead>
<tbody>
{rows_html}
</tbody>
</table>

<p class="meta" style="margin-top:1.4em">
Source files in <code>data/predictions/ablation_*.json</code> + <code>data/predictions/multi_outcome_retrieval{{.phase1_sonnet,}}.json</code>.
Ground truth in <code>data/resolved.json</code>. Full methodology + per-decision postmortem in <code>docs/DECISIONS.md</code>.
<a href="/dashboard">← back to live dashboard</a>
</p>

</div>
</body></html>"""
    response = HTMLResponse(html)
    _set_dashboard_cookie_if_needed(response, request)
    return response


_OPEN_COMPARE_MODELS: list[tuple[str, str]] = [
    ("Opus 4.7 prod", "open_{dataset}.json"),
    ("Opus 4.6", "ablation_open_{dataset}_claude-opus-4-6.json"),
    ("Sonnet 4.6", "ablation_open_{dataset}_claude-sonnet-4-6.json"),
    ("GPT-5.2", "ablation_open_{dataset}_gpt-5-2.json"),
]


def _prediction_market_ticker(prediction: dict[str, Any]) -> str | None:
    return (
        prediction.get("market_ticker")
        or (prediction.get("_event") or {}).get("market_ticker")
    )


def _load_open_predictions_by_model(
    pred_dir: Path,
    dataset: str,
) -> dict[str, dict[str, dict[str, Any]]]:
    by_model: dict[str, dict[str, dict[str, Any]]] = {}
    for label, template in _OPEN_COMPARE_MODELS:
        path = pred_dir / template.format(dataset=dataset)
        if not path.exists():
            by_model[label] = {}
            continue
        data = json.loads(path.read_text())
        preds = data.get("predictions", []) if isinstance(data, dict) else data
        rows: dict[str, dict[str, Any]] = {}
        for pred in preds:
            if not isinstance(pred, dict):
                continue
            ticker = _prediction_market_ticker(pred)
            if ticker:
                rows[ticker] = pred
        by_model[label] = rows
    return by_model


@app.get("/compare-open", response_class=HTMLResponse)
def compare_open(
    request: Request,
    _: None = Depends(_require_dashboard_auth_redirect),
) -> HTMLResponse:
    """Browse our production predictions on the 3 open PA datasets
    (sample-economics, sample-entertainment, sample-sports). 42 events,
    no actuals yet -- this is a research/showcase view, not a Brier table.

    Per Rob's ask: 'browse all these visually, see how or what my model
    said, find any bugs or such too'. Shows category + title + Opus 4.7's
    per-outcome probabilities + rationale + evidence URLs for every event.
    """
    repo_root = Path(__file__).resolve().parent
    pred_dir = repo_root / "data/predictions"
    dataset_dir = repo_root / "data/datasets"
    sources = [
        "sample-economics",
        "sample-entertainment",
        "sample-sports",
    ]
    sections_html: list[str] = []
    total = 0
    model_labels = [label for label, _ in _OPEN_COMPARE_MODELS]
    model_header = "".join(f"<span>{html_escape(label)}</span>" for label in model_labels)
    for ds_name in sources:
        dataset_path = dataset_dir / f"{ds_name}.json"
        if not dataset_path.exists():
            continue
        events = json.loads(dataset_path.read_text())
        if not isinstance(events, list):
            continue
        by_model = _load_open_predictions_by_model(pred_dir, ds_name)
        cards = []
        event_cards: list[tuple[float, str]] = []
        for ev in events:
            ticker = ev.get("market_ticker") or ev.get("event_ticker") or "?"
            prod = by_model.get("Opus 4.7 prod", {}).get(ticker, {})
            title = ev.get("title") or prod.get("title", "")
            cat = ev.get("category") or prod.get("category", "?")
            close_time = ev.get("close_time", "")
            probs = prod.get("probabilities") or []
            rationale = (prod.get("rationale") or "")[:300]
            ev_urls = (prod.get("evidence_urls") or [])[:4]
            p_values: list[float] = []
            model_cells = []
            for label in model_labels:
                pred = by_model.get(label, {}).get(ticker, {})
                p_yes = pred.get("p_yes")
                if isinstance(p_yes, (int, float)):
                    p_float = max(0.0, min(1.0, float(p_yes)))
                    p_values.append(p_float)
                    pct = p_float * 100
                    tooltip = html_escape((pred.get("rationale") or "")[:220])
                    model_cells.append(
                        f"<span class='model-p' title='{tooltip}'>{pct:.0f}%</span>"
                    )
                else:
                    model_cells.append("<span class='model-p missing'>&mdash;</span>")
            spread = (max(p_values) - min(p_values)) if len(p_values) >= 2 else 0.0
            spread_class = "spread-high" if spread >= 0.25 else ("spread-mid" if spread >= 0.12 else "spread-low")
            prob_rows = []
            for pp in probs:
                pct = max(0.0, min(1.0, float(pp.get("probability", 0.0)))) * 100
                prob_rows.append(
                    f"<div class='prob-row'>"
                    f"<span class='prob-label'>{html_escape(str(pp.get('market','?'))[:50])}</span>"
                    f"<span class='prob-bar'><span class='prob-fill' style='width:{pct:.1f}%'></span></span>"
                    f"<span class='prob-val'>{pct:.0f}%</span></div>"
                )
            ev_html = ""
            if ev_urls:
                hosts = []
                for u in ev_urls:
                    try:
                        from urllib.parse import urlparse
                        hosts.append(html_escape(urlparse(u).netloc))
                    except Exception:
                        pass
                ev_html = "<div class='evidence muted small'>Evidence: " + " · ".join(hosts) + "</div>"
            event_cards.append((spread,
                f"<div class='pred-card'>"
                f"<div class='pred-head'>"
                f"<span class='cat-pill'>{html_escape(cat)}</span>"
                f"<code class='small muted'>{html_escape(ticker[:48])}</code>"
                f"<span class='small muted'>closes {html_escape(close_time[:10])}</span>"
                f"<span class='spread-pill {spread_class}'>spread {spread:.2f}</span>"
                f"</div>"
                f"<div class='pred-title'>{html_escape(title[:140])}</div>"
                f"<div class='model-grid'><div class='model-grid-head'>{model_header}</div>"
                f"<div class='model-grid-row'>{''.join(model_cells)}</div></div>"
                f"<div class='pred-bars'>{''.join(prob_rows)}</div>"
                f"<div class='pred-rationale'>{html_escape(rationale)}</div>"
                f"{ev_html}"
                f"</div>"
            ))
        event_cards.sort(key=lambda item: item[0], reverse=True)
        cards = [html for _, html in event_cards]
        total += len(events)
        sections_html.append(
            f"<h2>{ds_name} <span class='muted small'>({len(events)} events)</span></h2>"
            f"<div class='pred-list'>{''.join(cards)}</div>"
        )
    body_html = "\n".join(sections_html) or "<p>No open-event predictions yet.</p>"

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForecastingPath · Open-event predictions</title>
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<style>
  :root {{
    --bg: #f7f8fb; --panel: #ffffff; --border: #d8dde6;
    --text: #111827; --muted: #5b6472; --accent: #1d4ed8;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text);
         font: 14px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif; }}
  .page {{ max-width: 1200px; margin: 0 auto; padding: 1.5em 1em 3em; }}
  h1 {{ margin: 0 0 0.4em; font-size: 1.7rem; }}
  h2 {{ margin: 1.6em 0 0.6em; font-size: 1.15rem; color: var(--accent);
        border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }}
  .meta {{ color: var(--muted); font-size: 0.92em; }}
  .muted {{ color: var(--muted); }}
  .small {{ font-size: 0.84em; }}
  .pred-list {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
                gap: 0.85em; }}
  .pred-card {{ background: var(--panel); border: 1px solid var(--border);
                border-radius: 8px; padding: 0.95em 1em; }}
  .pred-head {{ display: flex; gap: 0.5em; align-items: center;
                flex-wrap: wrap; margin-bottom: 0.5em; }}
  .cat-pill {{ background: #eef2ff; color: var(--accent); padding: 0.15em 0.55em;
               border-radius: 4px; font-size: 0.78em; font-weight: 700;
               text-transform: uppercase; letter-spacing: 0.03em; }}
  .spread-pill {{ padding: 0.15em 0.55em; border-radius: 4px;
                  font-size: 0.78em; font-weight: 700; }}
  .spread-low {{ background: #ecfdf5; color: #047857; }}
  .spread-mid {{ background: #fffbeb; color: #b45309; }}
  .spread-high {{ background: #fef2f2; color: #b91c1c; }}
  .pred-title {{ font-weight: 650; margin-bottom: 0.55em; line-height: 1.35; }}
  .model-grid {{ margin: 0.55em 0 0.7em; border: 1px solid var(--border);
                 border-radius: 6px; overflow: hidden; }}
  .model-grid-head, .model-grid-row {{ display: grid;
                 grid-template-columns: repeat(4, minmax(0, 1fr)); }}
  .model-grid-head span {{ background: #f1f3f7; color: var(--muted);
                 font-size: 0.72em; font-weight: 700; text-transform: uppercase;
                 padding: 0.4em 0.45em; border-right: 1px solid var(--border); }}
  .model-grid-row span {{ padding: 0.45em; border-top: 1px solid var(--border);
                 border-right: 1px solid var(--border); font-weight: 750;
                 font-variant-numeric: tabular-nums; text-align: center; }}
  .model-grid-head span:last-child, .model-grid-row span:last-child {{ border-right: 0; }}
  .model-p.missing {{ color: var(--muted); font-weight: 500; }}
  .pred-bars {{ display: flex; flex-direction: column; gap: 0.32em; }}
  .prob-row {{ display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(80px, 2fr) 44px;
               gap: 0.5em; align-items: center; font-size: 0.88em; }}
  .prob-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .prob-bar {{ height: 9px; background: #f1f3f7; border-radius: 4px; overflow: hidden;
               border: 1px solid #e2e6ed; }}
  .prob-fill {{ display: block; height: 100%; background: var(--accent); }}
  .prob-val {{ text-align: right; font-variant-numeric: tabular-nums;
               font-weight: 650; color: var(--text); }}
  .pred-rationale {{ margin-top: 0.55em; font-size: 0.88em;
                     color: var(--muted); line-height: 1.4; }}
  .evidence {{ margin-top: 0.35em; }}
  a {{ color: var(--accent); text-decoration: none; font-weight: 650; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head><body>
<div class="page">
<h1>Open-event predictions <span class="muted small">({total} events across 3 datasets)</span></h1>
<p class="meta">
Our production agent (Opus 4.7 + Brave retrieval + market-anchor prompt + 0.10 longshot floor)
run against PA's three open dataset releases — <code>sample-economics</code>, <code>sample-entertainment</code>,
<code>sample-sports</code>. These events are unresolved, so there's no Brier scoring yet —
this view is for inspecting the agent's reasoning on a wider variety of events than the resolved set.
For the scored comparison vs other models, see <a href="/compare">/compare</a>.
</p>
<p class="meta"><strong>Model agreement matrix:</strong> each card now shows p(outcome[0]) from Opus 4.7 production, Opus 4.6, Sonnet 4.6, and GPT-5.2. Cards are sorted by spread within each dataset so high-disagreement events rise to the top.</p>
{body_html}
<p class="meta" style="margin-top:2em">
Source: <code>data/predictions/open_sample-*.json</code> and <code>data/predictions/ablation_open_*.json</code>. <a href="/dashboard">← back to live dashboard</a>
</p>
</div>
</body></html>"""
    response = HTMLResponse(html)
    _set_dashboard_cookie_if_needed(response, request)
    return response


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


@app.get("/events")
async def events_stream(
    _: None = Depends(_require_dashboard_auth),
) -> StreamingResponse:
    """Server-Sent Events endpoint. /dashboard subscribes to push updates.

    Each new prediction served by /predict gets fanned out to every
    subscriber. Heartbeat pings every 25 seconds keep proxies happy.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _SSE_SUBSCRIBERS.append(q)

    async def gen():
        try:
            # Initial snapshot
            yield (
                "event: hello\ndata: "
                + json.dumps({
                    "total_predictions": _TOTAL_PREDICTIONS,
                    "total_cost_usd": round(_TOTAL_COST_USD, 4),
                    "variant": _VARIANT_NAME,
                })
                + "\n\n"
            )
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"event: {msg.get('type','message')}\ndata: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            try:
                _SSE_SUBSCRIBERS.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _backtest_summary_for_dashboard() -> list[dict]:
    """Read backtest_summary.json + reports/calibration_summary.md if present.

    Returns a list of {variant, brier, n} dicts, sorted by Brier ascending.
    Used for the per-variant comparison bar chart on the dashboard.
    """
    paths = [
        Path("data/predictions/backtest_summary.json"),
        Path(__file__).resolve().parent / "data/predictions/backtest_summary.json",
    ]
    for p in paths:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            rows = []
            for r in data:
                ev = r.get("evaluator_result") or {}
                rows.append({
                    "variant": r.get("variant", "?"),
                    "brier": ev.get("brier_score") or r.get("brier_local"),
                    "n": r.get("n_predictions", 0),
                })
            rows = [r for r in rows if r["brier"] is not None]
            rows.sort(key=lambda r: r["brier"])
            return rows
        except Exception:
            continue
    return []


def _svg_sparkline(values: list[int], width: int = 220, height: int = 36) -> str:
    """Tiny inline SVG sparkline. values is a list of counts per minute."""
    if not values:
        return f"<svg width='{width}' height='{height}'><text x='6' y='22' fill='#8694b3' font-size='11'>no activity yet</text></svg>"
    n = len(values)
    vmax = max(values) or 1
    pts = []
    for i, v in enumerate(values):
        x = (i / max(1, n - 1)) * (width - 4) + 2
        y = height - 4 - (v / vmax) * (height - 8)
        pts.append(f"{x:.1f},{y:.1f}")
    path = "M " + " L ".join(pts)
    fill_path = path + f" L {width-2},{height-4} L 2,{height-4} Z"
    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
        f"<path d='{fill_path}' fill='url(#sg)' opacity='0.35'/>"
        f"<path d='{path}' stroke='#38bdf8' stroke-width='1.5' fill='none'/>"
        f"<defs><linearGradient id='sg' x1='0' x2='0' y1='0' y2='1'>"
        f"<stop offset='0' stop-color='#38bdf8'/>"
        f"<stop offset='1' stop-color='#38bdf8' stop-opacity='0'/>"
        f"</linearGradient></defs>"
        f"</svg>"
    )


def _svg_brier_bars(rows: list[dict], width: int = 540, row_h: int = 22) -> str:
    """Horizontal bar chart of per-variant Brier (lower is better)."""
    if not rows:
        return "<div class='muted small'>no backtest data yet (run scripts/backtest_forecast.py)</div>"
    rows = rows[:14]  # cap
    height = len(rows) * row_h + 24
    bmax = max(r["brier"] for r in rows) or 1.0
    bmin = min(r["brier"] for r in rows)
    bars = []
    for i, r in enumerate(rows):
        y = 12 + i * row_h
        bw = (r["brier"] / bmax) * (width - 220)
        # color: best (lowest) = bright accent; worst = muted
        is_best = i == 0
        bar_color = "#10b981" if is_best else "#6366f1"
        text_color = "#111827" if is_best else "#374151"
        bars.append(
            f"<rect x='180' y='{y-9}' width='{bw:.1f}' height='14' fill='{bar_color}' opacity='0.85' rx='2'/>"
            f"<text x='174' y='{y+2}' fill='{text_color}' font-size='11' text-anchor='end'>{html_escape(r['variant'])}</text>"
            f"<text x='{180+bw+6:.1f}' y='{y+2}' fill='#111827' font-size='11' font-variant-numeric='tabular-nums'>{r['brier']:.4f}</text>"
        )
    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
        f"<rect x='0' y='0' width='{width}' height='{height}' fill='transparent'/>"
        + "".join(bars)
        + "</svg>"
    )


def _uptime_human() -> str:
    delta = datetime.now(timezone.utc) - _SERVER_START_TS
    total = int(delta.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _prob_bars_html(probs: list[dict]) -> str:
    """Inline horizontal bars per outcome. Width % maps to probability."""
    if not probs:
        return "<span class='muted'>—</span>"
    rows = []
    for p in probs:
        market = html_escape(str(p.get("market", "?")))
        prob = max(0.0, min(1.0, float(p.get("probability", 0.0))))
        rows.append(
            f"<div class='prob-row'>"
            f"<span class='prob-label'>{market}</span>"
            f"<span class='prob-bar'><span class='prob-fill' style='width:{prob*100:.1f}%'></span></span>"
            f"<span class='prob-val'>{prob*100:.0f}%</span>"
            f"</div>"
        )
    return "".join(rows)


def _dashboard_first_call_triage_html(history: list[dict[str, Any]]) -> str:
    """Operator checklist for the first live Prophet Arena request."""
    latest = history[0] if history else None
    if latest:
        probs = latest.get("probabilities") if isinstance(latest.get("probabilities"), list) else []
        outcomes = latest.get("outcomes") if isinstance(latest.get("outcomes"), list) else []
        outcome_count = len(outcomes) or len(probs)
        trace = latest.get("trace") if isinstance(latest.get("trace"), dict) else {}
        latency = trace.get("latency_ms") if isinstance(trace, dict) else {}
        total_latency = "not recorded"
        if isinstance(latency, dict) and latency.get("total") is not None:
            total_latency = f"{int(latency.get('total', 0))} ms"
        parse_path = str(trace.get("parse_path") or trace.get("parser_path") or "not recorded")
        warnings_raw = trace.get("warnings") if isinstance(trace, dict) else None
        if isinstance(warnings_raw, list):
            warnings = "; ".join(str(w) for w in warnings_raw[:4]) or "none"
        elif warnings_raw:
            warnings = str(warnings_raw)
        else:
            warnings = "none"
        evidence = latest.get("evidence_urls") if isinstance(latest.get("evidence_urls"), list) else []
        status_line = "PA activity observed"
        rows = [
            ("Event", str(latest.get("title") or latest.get("market_ticker") or "latest prediction")[:110]),
            ("Outcome count", f"{outcome_count} outcomes" if outcome_count else "not recorded"),
            ("Total latency", total_latency),
            ("Parse path", parse_path),
            ("Warnings", warnings),
            ("Evidence URLs", f"{len(evidence)} evidence URLs"),
        ]
    else:
        status_line = "Waiting for first Prophet Arena call"
        rows = [
            ("Event", "not received"),
            ("Outcome count", "check immediately after first payload"),
            ("Total latency", "must remain far below PA timeout"),
            ("Parse path", "confirm direct parse or repair path"),
            ("Warnings", "inspect before changing model or prompt"),
            ("Evidence URLs", "confirm retrieval actually ran"),
        ]
    body = "".join(
        f"<tr><td>{html_escape(label)}</td><td>{html_escape(value)}</td></tr>"
        for label, value in rows
    )
    return (
        "<div class='card triage-card'>"
        f"<p><strong>{status_line}.</strong> First live payload review should focus on schema, latency, parser path, warnings, and evidence coverage.</p>"
        "<table><tbody>"
        f"{body}"
        "</tbody></table>"
        "<p class='meta'><strong>Do not change production variant</strong> until this table shows a real failure mode and a measured alternative beats it.</p>"
        "</div>"
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    _: None = Depends(_require_dashboard_auth_redirect),
) -> HTMLResponse:
    """Live HTML dashboard. Auto-refreshes every 30s."""
    remote = _fetch_remote_state()
    ep = remote.get("endpoint") or {}
    scores = remote.get("scores") or {}
    open_events = remote.get("open_events") or []
    if isinstance(open_events, dict) and "error" in open_events:
        open_events_list: list = []
    else:
        open_events_list = open_events if isinstance(open_events, list) else []
    dashboard_history = list(_PREDICTION_HISTORY)

    # Predictions table with inline probability bars and evidence URLs.
    pred_cards = ""
    for p in dashboard_history[:20]:
        probs_html = _prob_bars_html(p.get("probabilities") or [])
        evidence = p.get("evidence_urls") or []
        evidence_html = ""
        if evidence:
            links = " · ".join(
                f"<a href='{html_escape(u)}' target='_blank' rel='noopener'>{html_escape(u.split('/')[2] if '/' in u else u[:40])}</a>"
                for u in evidence[:5]
            )
            evidence_html = f"<div class='evidence'>Evidence: {links}</div>"
        pred_cards += (
            f"<div class='pred-card'>"
            f"<div class='pred-head'>"
            f"<span class='pred-ts'>{html_escape(p['ts'][11:19])}</span>"
            f"<span class='cat-pill'>{html_escape(p.get('category','?'))}</span>"
            f"<code class='pred-ticker'>{html_escape(p['market_ticker'])}</code>"
            f"</div>"
            f"<div class='pred-title'>{html_escape(p['title'][:140])}</div>"
            f"<div class='pred-bars'>{probs_html}</div>"
            f"<div class='pred-rationale'>{html_escape(p['rationale'][:240])}</div>"
            f"{evidence_html}"
            f"</div>"
        )
    if not pred_cards:
        pred_cards = (
            "<div class='empty'>no predictions served yet — waiting for Prophet Arena to call <code>/predict</code></div>"
        )

    # Open events table
    open_rows = ""
    for e in open_events_list[:10]:
        open_rows += (
            f"<tr><td><code>{html_escape(e.get('market_ticker','?'))}</code></td>"
            f"<td><span class='cat-pill'>{html_escape(e.get('category','?'))}</span></td>"
            f"<td>{html_escape((e.get('title') or '?')[:90])}</td>"
            f"<td class='muted small'>{html_escape((e.get('close_time') or '?')[:19])}</td></tr>"
        )
    if not open_rows:
        open_rows = (
            "<tr><td colspan='4' style='text-align:center;color:#888;padding:1em'>"
            "no open events right now — Prophet Arena hasn't posted any yet</td></tr>"
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
        scores_block = f"<div class='muted'>scores fetch error: {html_escape(scores['error'][:120])}</div>"
    elif scores and isinstance(scores, list) and len(scores) > 0:
        score_rows = ""
        for s in scores[:10]:
            highlight = "row-self" if s.get("team_name") == "CanadaHacks" else ""
            score_rows += (
                f"<tr class='{highlight}'>"
                f"<td>{html_escape(s.get('team_name','?'))}</td>"
                f"<td class='num'>{s.get('brier_score','—')}</td>"
                f"<td class='num'>{s.get('n_predictions','—')}</td>"
                f"<td class='num'>{s.get('n_matched','—')}</td>"
                f"</tr>"
            )
        scores_block = (
            "<table><thead><tr><th>team</th><th>brier</th>"
            "<th>n_pred</th><th>n_matched</th></tr></thead>"
            f"<tbody>{score_rows}</tbody></table>"
        )
    else:
        scores_block = "<div class='muted'>no scores yet — leaderboard fires after Prophet Arena scores at least one resolved event</div>"

    variant_desc = _VARIANT_DESCRIPTIONS.get(_VARIANT_NAME, "(no description)")
    cost_per_event = _VARIANT_COSTS.get(_VARIANT_NAME, 0.0)
    avg_p_dev = 0.0
    if dashboard_history:
        avg_p_dev = sum(abs(float(p.get("p_yes", 0.5)) - 0.5) for p in dashboard_history) / len(dashboard_history)

    # Sparkline + bar chart pieces
    spark_values = list(_PREDICTIONS_PER_MIN) + [_PPM_CURRENT_COUNT]
    spark_svg = _svg_sparkline(spark_values)
    brier_svg = _svg_brier_bars(_backtest_summary_for_dashboard())

    # Category distribution from recent predictions (for a tiny donut)
    cat_counts: dict[str, int] = {}
    for p in dashboard_history:
        c = p.get("category") or "?"
        cat_counts[c] = cat_counts.get(c, 0) + 1
    cat_total = sum(cat_counts.values()) or 1
    cat_chips = "".join(
        f"<span class='cat-chip'><strong>{html_escape(k)}</strong> {v}</span>"
        for k, v in sorted(cat_counts.items(), key=lambda x: -x[1])
    ) or "<span class='muted small'>none yet</span>"

    waiting_banner = ""
    if last_run == "—":
        waiting_banner = (
            "<div class='banner waiting'>"
            "<strong>Waiting for first call.</strong> Prophet Arena has not yet "
            "sent us any events. When they do, predictions will appear below in "
            "real time."
            "</div>"
        )
    first_call_triage_html = _dashboard_first_call_triage_html(dashboard_history)

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<!-- Auto-refresh DISABLED. Was every 30s, wiping the try-form result
     after a user submitted a prediction (Rob hit this 2026-05-16:
     "result disappeared or glitched"). Page state updates via the
     SSE /events stream now, so the meta refresh was redundant and
     destructive. -->
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Oracles · ForecastingPath live dashboard</title>
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" sizes="192x192" href="/static/icon-192.png">
<meta property="og:title" content="ForecastingPath · live dashboard">
<meta property="og:description" content="Live forecasting agent for Prophet Hacks 2026: Brave-search retrieval + Claude Opus 4.7 + Kalshi longshot guard.">
<meta property="og:image" content="https://forecastingpath.com/static/banner.webp">
<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body, {{delimiters: [
    {{left: '$$', right: '$$', display: true}},
    {{left: '$', right: '$', display: false}}
  ]}});"></script>
<style>
  :root {{
    --bg: #f7f8fb;
    --panel: #ffffff;
    --panel-2: #f1f3f7;
    --border: #d8dde6;
    --text: #111827;
    --text-2: #374151;
    --muted: #6b7280;
    --accent: #1d4ed8;
    --accent-soft: #dbeafe;
    --ok: #047857;
    --ok-soft: #d1fae5;
    --bad: #b91c1c;
    --bad-soft: #fee2e2;
    --warn: #b45309;
    --warn-soft: #fef3c7;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{ font: 16px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif;
         color: var(--text); background: var(--bg); }}
  .page {{ max-width: 1080px; margin: 0 auto; padding: 1.5em 1.4em 4em; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  h1 {{ font-size: 1.7em; margin: 0 0 0.15em; font-weight: 700; letter-spacing: 0; }}
  h2 {{ font-size: 1.15em; margin: 2em 0 0.6em; font-weight: 700; border-bottom: 2px solid var(--border); padding-bottom: 0.3em; }}
  p, li, td {{ font-size: 1em; line-height: 1.55; color: var(--text-2); }}
  .meta {{ color: var(--muted); font-size: 0.92em; }}
  .small {{ font-size: 0.92em; }}
  code {{ background: var(--panel-2); padding: 1px 6px; border-radius: 4px; font-size: 0.95em; color: var(--text); }}

  .topline {{ display: flex; flex-wrap: wrap; gap: 0.6em 1.2em; align-items: center; margin-bottom: 0.5em; color: var(--muted); }}
  .topline strong {{ color: var(--text); }}
  .live-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: var(--ok); margin-right: 6px; animation: pulse 2s infinite; }}
  .live-dot.warn {{ background: var(--warn); }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.45; }} }}

  .banner {{ padding: 1em 1.2em; border-radius: 8px; margin: 1em 0; border: 1px solid var(--border); font-size: 1.02em; }}
  .banner.waiting {{ background: var(--warn-soft); border-color: #fcd34d; color: #78350f; }}
  .banner.active {{ background: var(--ok-soft); border-color: #6ee7b7; color: #064e3b; }}

  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.8em; margin: 1.2em 0; }}
  @media (max-width: 720px) {{ .kpis {{ grid-template-columns: repeat(2, 1fr); }} }}
  .kpi {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 0.9em 1em; min-width: 0; }}
  .kpi .label {{ font-size: 0.78em; text-transform: uppercase; color: var(--muted); letter-spacing: 0.06em; font-weight: 600; }}
  .kpi .value {{ font-size: 1.55em; font-weight: 700; margin-top: 0.25em; font-variant-numeric: tabular-nums; color: var(--text); }}
  .kpi .sub {{ font-size: 0.88em; color: var(--muted); margin-top: 0.15em; }}

  .pill {{ display: inline-block; padding: 3px 10px; border-radius: 11px; font-size: 0.82em; font-weight: 700; }}
  .pill.ok {{ background: var(--ok-soft); color: var(--ok); }}
  .pill.bad {{ background: var(--bad-soft); color: var(--bad); }}
  .pill.warn {{ background: var(--warn-soft); color: var(--warn); }}
  .cat-pill {{ display: inline-block; padding: 1px 8px; border-radius: 9px; font-size: 0.82em; font-weight: 600;
               background: var(--accent-soft); color: var(--accent); }}

  table {{ border-collapse: collapse; width: 100%; margin: 0.4em 0 1em; font-size: 0.97em; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 0.65em 0.85em; border-bottom: 1px solid var(--border); vertical-align: top; }}
  th {{ background: var(--panel-2); font-weight: 700; font-size: 0.85em; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }}
  tr:last-child td {{ border-bottom: 0; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tr.row-self {{ background: var(--accent-soft); font-weight: 600; }}

  .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 1em 1.2em; margin-bottom: 0.7em; min-width: 0; overflow-x: auto; }}
  .card h3 {{ margin: 0 0 0.5em; font-size: 1em; font-weight: 700; }}
  .card p {{ margin: 0.4em 0; }}

  .pipeline {{ display: flex; flex-wrap: wrap; gap: 0.4em 0.5em; align-items: center; margin-top: 0.6em; }}
  .pipeline span.step {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 6px; padding: 5px 10px; font-size: 0.9em; max-width: 100%; overflow-wrap: anywhere; }}
  .pipeline span.arrow {{ color: var(--muted); font-weight: 700; }}

  .pred-list {{ display: grid; grid-template-columns: 1fr; gap: 0.8em; }}
  @media (min-width: 800px) {{ .pred-list {{ grid-template-columns: 1fr 1fr; }} }}
  .pred-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 0.95em 1.1em; }}
  .pred-head {{ display: flex; gap: 0.6em; align-items: baseline; font-size: 0.88em; color: var(--muted); margin-bottom: 0.5em; }}
  .pred-ts {{ font-variant-numeric: tabular-nums; }}
  .pred-ticker {{ margin-left: auto; font-size: 0.85em; }}
  .pred-title {{ font-weight: 600; color: var(--text); font-size: 1.02em; line-height: 1.4; margin-bottom: 0.6em; }}
  .prob-row {{ display: grid; grid-template-columns: minmax(100px, 1.3fr) 3fr 50px; gap: 0.6em; align-items: center; margin: 0.35em 0; font-size: 0.95em; }}
  .prob-label {{ color: var(--text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 500; }}
  .prob-bar {{ background: var(--panel-2); height: 18px; border-radius: 9px; overflow: hidden; border: 1px solid var(--border); }}
  .prob-fill {{ display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #3b82f6); }}
  .prob-val {{ font-variant-numeric: tabular-nums; text-align: right; color: var(--text); font-weight: 700; }}
  .pred-rationale {{ color: var(--text-2); font-size: 0.93em; line-height: 1.5; margin-top: 0.6em; padding-top: 0.6em; border-top: 1px solid var(--border); }}
  .evidence {{ font-size: 0.88em; color: var(--muted); margin-top: 0.5em; }}
  .evidence a {{ margin-right: 0.6em; }}
  .empty {{ text-align: center; color: var(--muted); padding: 2em; background: var(--panel); border-radius: 8px; border: 1px dashed var(--border); font-size: 1em; overflow-wrap: anywhere; }}

  form.try {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 1em 1.2em; }}
  form.try label {{ display: block; font-size: 0.9em; color: var(--muted); margin: 0.8em 0 0.3em; font-weight: 600; }}
  form.try input {{ width: 100%; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 0.55em 0.7em; font: inherit; }}
  form.try input:focus {{ outline: 2px solid var(--accent); outline-offset: -1px; border-color: var(--accent); }}
  form.try button {{ background: var(--accent); color: white; border: 0; padding: 0.7em 1.4em; border-radius: 6px; font: inherit; font-weight: 700; margin-top: 1em; cursor: pointer; font-size: 1em; }}
  form.try button:hover {{ background: #1e40af; }}
  #try-result {{ background: var(--panel-2); padding: 0.9em; border-radius: 6px; margin-top: 1em; font-family: ui-monospace, "SF Mono", monospace; font-size: 0.85em; white-space: pre-wrap; word-break: break-all; line-height: 1.4; color: var(--text-2); border: 1px solid var(--border); }}
  .demo-actions {{ display: flex; flex-wrap: wrap; gap: 0.7em; align-items: center; margin-top: 0.9em; }}
  .demo-actions button {{ background: var(--accent); color: #fff; border: 0; padding: 0.65em 1.1em; border-radius: 6px; font: inherit; font-weight: 700; cursor: pointer; }}
  .demo-actions button:disabled {{ opacity: 0.55; cursor: not-allowed; }}
  #demo-console, #demo-result {{ background: #0f172a; color: #dbeafe; border-radius: 6px; padding: 0.85em; margin-top: 0.8em; font-family: ui-monospace, "SF Mono", monospace; font-size: 0.84em; line-height: 1.45; white-space: pre-wrap; word-break: break-word; min-height: 3.2em; }}
  #demo-result {{ background: var(--panel-2); color: var(--text-2); border: 1px solid var(--border); }}
  .link-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.8em; margin-top: 0.8em; }}
  .link-card {{ display: block; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 0.85em 1em; color: var(--text); text-decoration: none; min-height: 104px; }}
  .link-card:hover {{ border-color: var(--accent); text-decoration: none; }}
  .link-card strong {{ display: block; color: var(--text); margin-bottom: 0.25em; }}
  .link-card span {{ color: var(--muted); font-size: 0.9em; line-height: 1.4; }}

  @keyframes flash {{ 0% {{ background: var(--ok-soft); }} 100% {{ background: var(--panel); }} }}
  .pred-card.fresh {{ animation: flash 1.8s ease-out; }}

  .math-box {{ background: var(--panel-2); border: 1px solid var(--border); border-left: 4px solid var(--accent); border-radius: 6px; padding: 0.7em 1.1em; margin: 0.6em 0; font-size: 0.97em; overflow-x: auto; }}
  .math-box .label {{ font-size: 0.85em; color: var(--muted); font-weight: 600; margin-bottom: 0.4em; }}
  .math-box .katex-display {{ margin: 0; overflow-x: auto; overflow-y: hidden; }}
  svg {{ max-width: 100%; height: auto; }}
  @media (max-width: 520px) {{
    .page {{ padding: 1.3em 1.05em 3em; }}
    .kpis {{ gap: 0.75em; }}
    .kpi {{ padding: 0.85em 0.9em; }}
    .kpi .label {{ font-size: 0.74em; overflow-wrap: anywhere; }}
    .kpi .value {{ font-size: 1.45em; overflow-wrap: anywhere; }}
    th, td {{ padding: 0.55em 0.65em; overflow-wrap: anywhere; }}
    table {{ display: block; overflow-x: auto; }}
    .prob-row {{ grid-template-columns: minmax(0, 1.2fr) minmax(80px, 2fr) 44px; gap: 0.45em; }}
    .link-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head><body>
<div class="page">

<h1>The Oracles</h1>
<div class="topline">
  <span><span class="live-dot" id="live-dot"></span><strong id="live-status">Live</strong></span>
  <span>team: <strong>CanadaHacks</strong></span>
  <span>variant: <strong>{html_escape(_VARIANT_NAME)}</strong></span>
  <span>uptime: <strong>{_uptime_human()}</strong></span>
  <span>commit: <strong><code>{html_escape(_BUILD_COMMIT_SHA)}</code></strong></span>
  <span><a href="https://github.com/Robby955/prophet-hacks">GitHub</a></span>
</div>

<p class="meta">A calibrated forecasting agent for Prophet Hacks 2026. Each event we receive is enriched with web evidence, scored by Claude Opus 4.7 with explicit market-odds anchoring, and protected by a Kalshi longshot floor before the probabilities are returned.</p>

{waiting_banner}

<h2>Status right now</h2>
<div class="kpis">
  <div class="kpi"><div class="label">Endpoint</div><div class="value">{endpoint_pill}</div><div class="sub">registered with Prophet Arena</div></div>
  <div class="kpi"><div class="label">Predictions served</div><div class="value">{_TOTAL_PREDICTIONS}</div><div class="sub">since process start</div></div>
  <div class="kpi"><div class="label">API spend</div><div class="value">${_TOTAL_COST_USD:.3f}</div><div class="sub">~${cost_per_event:.4f} per event</div></div>
  <div class="kpi"><div class="label">Last call from Prophet Arena</div><div class="value">{html_escape(last_run[:10]) if last_run != '—' else 'never'}</div><div class="sub">{html_escape(str(last_status))}</div></div>
</div>

<h2>First-call triage</h2>
{first_call_triage_html}

<h2>What our agent does</h2>
<div class="card">
  <figure style="margin:0 0 1.2em;">
    <img src="/static/howagentworks.webp" alt="ForecastingPath agent architecture: ingest event payload, gather high-signal web evidence, prioritize and deduplicate sources, estimate per-outcome probabilities with an LLM, apply longshot safeguard, return structured JSON" style="display:block;width:100%;height:auto;border-radius:8px;border:1px solid var(--border);">
    <figcaption class="meta" style="margin-top:0.5em;text-align:center;font-size:0.86em;">Pipeline overview. Each event flows through six stages, every stage logged and recoverable.</figcaption>
  </figure>
  <p>The text form, for accessibility and detail:</p>
  <div class="pipeline">
    <span class="step">1. Receive event JSON</span>
    <span class="arrow">›</span>
    <span class="step">2. Search Brave for top 5 evidence URLs</span>
    <span class="arrow">›</span>
    <span class="step">3. Dedupe by source priority (.gov / .edu first)</span>
    <span class="arrow">›</span>
    <span class="step">4. Opus 4.7 reads evidence (anchors on any cited market odds), assigns per-outcome probability</span>
    <span class="arrow">›</span>
    <span class="step">5. Kalshi longshot guard floors low values</span>
    <span class="arrow">›</span>
    <span class="step">6. Return JSON to scoring server</span>
  </div>
  <p style="margin-top:0.8em">Scoring is the standard Brier score, averaged across all outcomes per event:</p>
  <div class="math-box">
    <div class="label">Per-event Brier (lower is better)</div>
    $$ \\mathrm{{Brier}}_{{e}} = \\sum_{{o \\in O_e}} \\bigl( p_o - \\mathbb{{1}}[o = \\text{{winner}}] \\bigr)^2 $$
  </div>
  <div class="math-box">
    <div class="label">Kalshi longshot guard (applied per outcome)</div>
    $$ p_o \\;\\gets\\; \\max\\!\\left(p_o, \\; \\min\\!\\left(0.10, \\; \\max\\!\\left(0.05, \\frac{{0.5}}{{|O_e|}}\\right)\\right)\\right) $$
  </div>
  <p class="meta">Why the guard: Whelan's analysis of Kalshi shows buyers of contracts priced under $0.10 lose &gt;60% on average. LLMs are especially prone to dropping unlikely outcomes to near-zero on vivid narratives, so we floor them.</p>
</div>

<h2>Recent predictions (<span id="pred-count">{len(dashboard_history)}</span>)</h2>
<div class="pred-list" id="pred-grid">{pred_cards}</div>

<h2>Open events on Prophet Arena ({len(open_events_list)})</h2>
<table>
<thead><tr><th>market_ticker</th><th>category</th><th>title</th><th>close_time</th></tr></thead>
<tbody>{open_rows}</tbody>
</table>

<h2>Leaderboard</h2>
{scores_block}

<h2>Try a prediction yourself</h2>
<p class="meta">Sends an event-shaped request to our production endpoint. You see the exact JSON Prophet Arena gets. Pick an example or write your own — the pipeline does Brave search, Opus 4.7 reads evidence with market-odds anchoring, longshot guard caps low values.</p>
<form class="try" onsubmit="event.preventDefault(); doTry();">
  <label>Load an example</label>
  <select id="example-select" onchange="loadExample(this.value)">
    <option value="">— pick one or write your own below —</option>
    <option value="fed">Fed rate cut (binary, Economics)</option>
    <option value="superbowl">Super Bowl LXI winner (multi-outcome, Sports)</option>
    <option value="election">US 2028 election outcome (multi-outcome, Politics)</option>
    <option value="agi">AGI declared by 2030 (binary, Tech)</option>
    <option value="weather">UK record-hot July 2026 (binary, Climate)</option>
  </select>

  <label>Event title <span class="meta">(required, plain English question)</span></label>
  <input id="ti" value="Will the US Federal Reserve cut rates at the December 2026 meeting?" required>

  <label>Category</label>
  <input id="ca" value="Economics">

  <label>Outcomes <span class="meta">(comma-separated, at least 2)</span></label>
  <input id="ou" value="Yes, No" required>

  <label>Close time <span class="meta">(ISO 8601, must be in the future)</span></label>
  <input id="ct" value="2026-12-31T23:59:59Z" required>

  <label>Description <span class="meta">(optional, helps the model)</span></label>
  <textarea id="ds" rows="2" placeholder="Background context that helps the agent understand the question."></textarea>

  <label>Rules <span class="meta">(optional, resolution criteria)</span></label>
  <textarea id="rs" rows="2" placeholder="Exactly how does this resolve? e.g. 'YES if the FOMC announces a rate cut at the December 2026 meeting.'"></textarea>

  <button type="submit">Predict</button>
  <div id="try-result">Submit a question to see live per-outcome probabilities (~5–10 seconds: one Brave search + one Opus 4.7 call).</div>
</form>

<h2>Pipeline demo</h2>
<div class="card">
  <p class="meta">Runs one synthetic event through the production forecast pipeline and streams stage updates to this page. This is separate from Prophet Arena calls and does not change the production variant.</p>
  <div class="demo-actions">
    <button type="button" id="demo-start-button" onclick="startDemo()">Run pipeline demo</button>
    <span class="meta">Route: <code>POST /demo/start</code> -> <code>/demo/stream/&lt;run_id&gt;</code> -> <code>/demo/result/&lt;run_id&gt;</code></span>
  </div>
  <div id="demo-console">No demo run yet.</div>
  <div id="demo-result">Result JSON appears here after completion.</div>
</div>

<h2>Variant comparison (26-event backtest)</h2>
<div class="card">
  {brier_svg}
  <p class="meta" style="margin-top:0.8em">Brier here is the legacy single-<em>p</em> metric from the local backtest. The <strong>multi_outcome_retrieval</strong> 0.064 number is contaminated by data leakage (Brave can find articles about resolved past events); live performance on future events does not leak.</p>
</div>

<h2>Private research views</h2>
<p class="meta">These pages are dashboard-auth gated. They are meant for operator review, model debugging, and submission prep, not the public landing page during active scoring.</p>
<div class="link-grid">
  <a class="link-card" href="/review"><strong>Judge review brief</strong><span>One-page demo script, likely questions, current proof, and first-call checklist.</span></a>
  <a class="link-card" href="/observatory"><strong>Observatory</strong><span>Live commit, persisted traces, experiment board, and adversarial-review answers.</span></a>
  <a class="link-card" href="/static/summary.html"><strong>Summary report</strong><span>Brier table, bootstrap interval, phase decomposition, calibration plot, and findings.</span></a>
  <a class="link-card" href="/static/gallery_resolved.html"><strong>Resolved gallery</strong><span>Side-by-side per-event losses across production and alternative model runs.</span></a>
  <a class="link-card" href="/static/gallery_open.html"><strong>Open-event gallery</strong><span>Model spread on unresolved events for disagreement triage before PA scoring.</span></a>
  <a class="link-card" href="/compare"><strong>Comparison grid</strong><span>Auth-gated model comparison with reliability diagram and event-level rationales.</span></a>
  <a class="link-card" href="/compare-open"><strong>Open comparison</strong><span>Current open-event matrix for checking model agreement and likely failure modes.</span></a>
</div>

<h2>Quick links</h2>
<ul>
<li><strong><a href="/compare">/compare</a></strong> — multi-model multi-event comparison grid (26 resolved + 5 models, color-coded by Brier)</li>
<li><strong><a href="/compare-open">/compare-open</a></strong> — Opus 4.7 predictions on the 42 open events from sample-economics/-entertainment/-sports</li>
<li><code><a href="/healthz">/healthz</a></code> — server health JSON</li>
<li><code><a href="/predict">/predict</a></code> — the actual endpoint (POST)</li>
<li><code><a href="/predictions">/predictions</a></code> — last 50 predictions JSON</li>
<li><code><a href="/events">/events</a></code> — Server-Sent Events live stream</li>
<li><a href="https://github.com/Robby955/prophet-hacks">GitHub repo</a></li>
</ul>

<p class="meta" style="margin-top:2em">The page stays live through Server-Sent Events. New predictions stream in with a brief highlight animation; form results are not wiped by automatic refresh.</p>

</div>
<script>
const EXAMPLES = {{
  fed: {{ title: "Will the US Federal Reserve cut rates at the December 2026 meeting?",
         category: "Economics", outcomes: "Yes, No",
         close_time: "2026-12-31T23:59:59Z",
         description: "FOMC meets in December 2026 to set the federal funds rate.",
         rules: "YES if the FOMC announces a rate cut at the December 2026 meeting; otherwise NO." }},
  superbowl: {{ title: "Who wins Super Bowl LXI in February 2027?",
         category: "Sports", outcomes: "Kansas City Chiefs, Philadelphia Eagles, Baltimore Ravens, Buffalo Bills, San Francisco 49ers, Detroit Lions, Other",
         close_time: "2027-02-14T23:59:59Z",
         description: "Super Bowl LXI is the NFL championship game in February 2027.",
         rules: "Resolves to the team that wins Super Bowl LXI. 'Other' if winner is none of the listed teams." }},
  election: {{ title: "Which party wins the 2028 US Presidential Election?",
         category: "Politics", outcomes: "Democratic, Republican, Third party / Other",
         close_time: "2028-11-08T23:59:59Z",
         description: "US presidential election November 2028.",
         rules: "Resolves to the party of the candidate who wins a majority of Electoral College votes." }},
  agi: {{ title: "Will a top AI lab publicly declare AGI by end of 2030?",
         category: "Tech", outcomes: "Yes, No",
         close_time: "2030-12-31T23:59:59Z",
         description: "A 'top AI lab' means OpenAI, Anthropic, DeepMind, xAI, Meta, or similar major frontier lab.",
         rules: "YES if any top AI lab makes a formal public statement claiming to have achieved AGI by Dec 31, 2030." }},
  weather: {{ title: "Will any UK weather station record above 40C in July 2026?",
         category: "Climate", outcomes: "Yes, No",
         close_time: "2026-07-31T23:59:59Z",
         description: "Reference: UK record is 40.3C set July 2022 at Coningsby.",
         rules: "YES if at least one UK Met Office-recognized weather station records a temperature above 40.0C in July 2026." }},
}};

function loadExample(key) {{
  const ex = EXAMPLES[key];
  if (!ex) return;
  document.getElementById("ti").value = ex.title;
  document.getElementById("ca").value = ex.category;
  document.getElementById("ou").value = ex.outcomes;
  document.getElementById("ct").value = ex.close_time;
  document.getElementById("ds").value = ex.description;
  document.getElementById("rs").value = ex.rules;
}}

function validateInputs() {{
  const errs = [];
  const title = document.getElementById("ti").value.trim();
  if (!title || title.length < 10) errs.push("Title must be at least 10 characters.");
  const outs = document.getElementById("ou").value.split(",").map(s => s.trim()).filter(Boolean);
  if (outs.length < 2) errs.push("Need at least 2 outcomes (comma-separated).");
  const ct = document.getElementById("ct").value.trim();
  if (!/^\\d{{4}}-\\d{{2}}-\\d{{2}}T\\d{{2}}:\\d{{2}}:\\d{{2}}Z?$/.test(ct)) errs.push("Close time must be ISO 8601 (e.g. 2026-12-31T23:59:59Z).");
  try {{ if (new Date(ct).getTime() < Date.now()) errs.push("Close time should be in the future."); }} catch (_) {{}}
  return errs;
}}

async function doTry() {{
  const out = document.getElementById("try-result");
  const errs = validateInputs();
  if (errs.length) {{
    out.textContent = "Fix these first:\\n  - " + errs.join("\\n  - ");
    return;
  }}
  out.textContent = "calling /predict ... (Brave + Opus 4.7 typically takes 5–10s)";
  const outcomes = document.getElementById("ou").value.split(",").map(s => s.trim()).filter(Boolean);
  const body = {{
    event_ticker: "dashboard-try-" + Date.now(),
    market_ticker: "dashboard-try-" + Date.now(),
    title: document.getElementById("ti").value.trim(),
    category: document.getElementById("ca").value.trim() || "General",
    close_time: document.getElementById("ct").value.trim(),
    outcomes,
    description: document.getElementById("ds").value.trim() || null,
    rules: document.getElementById("rs").value.trim() || null,
  }};
  const t0 = performance.now();
  try {{
    const r = await fetch("/predict", {{method: "POST", headers: {{"content-type": "application/json"}}, body: JSON.stringify(body)}});
    const dt = ((performance.now() - t0)/1000).toFixed(1);
    if (!r.ok) {{
      const errBody = await r.text();
      out.textContent = `HTTP ${{r.status}} after ${{dt}}s\\n${{errBody.slice(0, 500)}}`;
      return;
    }}
    const j = await r.json();
    out.textContent = `latency: ${{dt}}s\\n` + JSON.stringify(j, null, 2);
  }} catch (e) {{ out.textContent = "network error: " + e.message; }}
}}

let demoSource = null;

function appendDemoLine(line) {{
  const consoleEl = document.getElementById("demo-console");
  consoleEl.textContent += (consoleEl.textContent ? "\\n" : "") + line;
}}

async function startDemo() {{
  const button = document.getElementById("demo-start-button");
  const consoleEl = document.getElementById("demo-console");
  const resultEl = document.getElementById("demo-result");
  if (demoSource) demoSource.close();
  button.disabled = true;
  consoleEl.textContent = "starting demo via /demo/start";
  resultEl.textContent = "waiting for result";
  try {{
    const started = await fetch("/demo/start", {{method: "POST"}});
    if (!started.ok) {{
      consoleEl.textContent = `start failed: HTTP ${{started.status}}\\n${{(await started.text()).slice(0, 500)}}`;
      button.disabled = false;
      return;
    }}
    const meta = await started.json();
    appendDemoLine(`run_id=${{meta.run_id}}`);
    appendDemoLine(`stream=${{meta.stream_url}} result=${{meta.result_url}}`);
    demoSource = new EventSource(meta.stream_url);
    demoSource.addEventListener("demo", async (ev) => {{
      let msg; try {{ msg = JSON.parse(ev.data); }} catch (_) {{ return; }}
      appendDemoLine(`${{msg.ts.slice(11,19)}}  ${{msg.stage}}  ${{msg.status}}  ${{msg.message}}`);
      if (msg.status === "completed" || msg.status === "failed") {{
        demoSource.close();
        demoSource = null;
        button.disabled = false;
        const result = await fetch(meta.result_url);
        const body = await result.json();
        resultEl.textContent = JSON.stringify(body.result || {{error: body.error, status: body.status}}, null, 2);
      }}
    }});
    demoSource.onerror = () => {{
      appendDemoLine("stream disconnected");
      if (demoSource) demoSource.close();
      demoSource = null;
      button.disabled = false;
    }};
  }} catch (e) {{
    consoleEl.textContent = "demo error: " + e.message;
    button.disabled = false;
  }}
}}

(function initSSE() {{
  if (!window.EventSource) return;
  const dot = document.getElementById("live-dot");
  const status = document.getElementById("live-status");
  const es = new EventSource("/events");
  es.addEventListener("hello", () => {{ status.textContent = "Live"; dot.classList.remove("warn"); }});
  es.addEventListener("prediction", (ev) => {{
    let msg; try {{ msg = JSON.parse(ev.data); }} catch(e) {{ return; }}
    const grid = document.getElementById("pred-grid");
    if (!grid) return;
    const card = document.createElement("div");
    card.className = "pred-card fresh";
    const probsHtml = (msg.probabilities || []).map(p => {{
      const pct = Math.max(0, Math.min(1, +p.probability)) * 100;
      return `<div class="prob-row"><span class="prob-label">${{p.market}}</span><span class="prob-bar"><span class="prob-fill" style="width:${{pct.toFixed(1)}}%"></span></span><span class="prob-val">${{pct.toFixed(0)}}%</span></div>`;
    }}).join("");
    const ev_urls = (msg.evidence_urls || []).slice(0, 4);
    const ev_html = ev_urls.length
      ? `<div class="evidence">Evidence: ${{ev_urls.map(u => {{ try {{ return `<a href="${{u}}" target="_blank" rel="noopener">${{new URL(u).host}}</a>`; }} catch (_) {{ return ""; }} }}).join("")}}</div>`
      : "";
    card.innerHTML = `
      <div class="pred-head"><span class="pred-ts">${{msg.ts.slice(11,19)}}</span><span class="cat-pill">${{msg.category||"?"}}</span><code class="pred-ticker">${{msg.market_ticker||"?"}}</code></div>
      <div class="pred-title">${{(msg.title||"").slice(0,160)}}</div>
      <div class="pred-bars">${{probsHtml}}</div>
      <div class="pred-rationale">${{(msg.rationale||"").slice(0,260)}}</div>
      ${{ev_html}}
    `;
    grid.insertBefore(card, grid.firstChild);
    while (grid.children.length > 20) grid.removeChild(grid.lastChild);
    document.getElementById("pred-count").textContent = msg.total_predictions;
  }});
  es.onerror = () => {{ status.textContent = "Reconnecting"; dot.classList.add("warn"); }};
}})();
</script>
</body></html>"""
    response = HTMLResponse(html)
    _set_dashboard_cookie_if_needed(response, request)
    return response


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
