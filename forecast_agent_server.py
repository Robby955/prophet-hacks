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

import asyncio
import collections
import json
import logging
import os
import time
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

load_dotenv()

import forecast_track  # noqa: E402


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
    "multi_outcome_retrieval": 0.012,
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
    "multi_outcome_retrieval": "Brave Search → 5 deduped evidence chunks → multi_outcome prompt → Kalshi longshot guard. The current production variant.",
    "hybrid_routed": "Binary (n<=2): gpt55. Multi (n>2): multi_outcome. Routes by outcome count to play each model's strength.",
}

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

    evidence_urls = result.get("evidence_urls") or []
    if not isinstance(evidence_urls, list):
        evidence_urls = []
    _PREDICTION_HISTORY.appendleft({
        "ts": datetime.now(timezone.utc).isoformat(),
        "market_ticker": event.market_ticker,
        "title": event.title,
        "category": event.category,
        "p_yes": p_yes,
        "outcomes": outcomes,
        "probabilities": probs,
        "rationale": rationale,
        "evidence_urls": evidence_urls[:8],
    })
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


@app.get("/events")
async def events_stream() -> StreamingResponse:
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
        text_color = "#e8edf5" if is_best else "#c7d2fe"
        bars.append(
            f"<rect x='180' y='{y-9}' width='{bw:.1f}' height='14' fill='{bar_color}' opacity='0.85' rx='2'/>"
            f"<text x='174' y='{y+2}' fill='{text_color}' font-size='11' text-anchor='end'>{html_escape(r['variant'])}</text>"
            f"<text x='{180+bw+6:.1f}' y='{y+2}' fill='#e8edf5' font-size='11' font-variant-numeric='tabular-nums'>{r['brier']:.4f}</text>"
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

    # Predictions table with inline probability bars and evidence URLs.
    pred_cards = ""
    for p in list(_PREDICTION_HISTORY)[:20]:
        probs_html = _prob_bars_html(p.get("probabilities") or [])
        evidence = p.get("evidence_urls") or []
        evidence_html = ""
        if evidence:
            links = " · ".join(
                f"<a href='{html_escape(u)}' target='_blank' rel='noopener'>{html_escape(u.split('/')[2] if '/' in u else u[:40])}</a>"
                for u in evidence[:5]
            )
            evidence_html = f"<div class='evidence'>📎 {links}</div>"
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
    if _PREDICTION_HISTORY:
        avg_p_dev = sum(abs(p["p_yes"] - 0.5) for p in _PREDICTION_HISTORY) / len(_PREDICTION_HISTORY)

    # Sparkline + bar chart pieces
    spark_values = list(_PREDICTIONS_PER_MIN) + [_PPM_CURRENT_COUNT]
    spark_svg = _svg_sparkline(spark_values)
    brier_svg = _svg_brier_bars(_backtest_summary_for_dashboard())

    # Category distribution from recent predictions (for a tiny donut)
    cat_counts: dict[str, int] = {}
    for p in _PREDICTION_HISTORY:
        c = p.get("category") or "?"
        cat_counts[c] = cat_counts.get(c, 0) + 1
    cat_total = sum(cat_counts.values()) or 1
    cat_chips = "".join(
        f"<span class='cat-chip'><strong>{html_escape(k)}</strong> {v}</span>"
        for k, v in sorted(cat_counts.items(), key=lambda x: -x[1])
    ) or "<span class='muted small'>none yet</span>"

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<title>The Oracles — dashboard</title>
<style>
  :root {{
    --bg: #0b1020;
    --panel: #131a30;
    --panel-2: #1a2240;
    --border: #25304d;
    --text: #e8edf5;
    --muted: #8694b3;
    --accent: #6366f1;
    --accent-2: #38bdf8;
    --ok: #10b981;
    --bad: #ef4444;
    --warn: #f59e0b;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font: 13px/1.5 -apple-system, system-ui, sans-serif;
         max-width: 1200px; margin: 1.5em auto; padding: 0 1em;
         color: var(--text); background: var(--bg); }}
  a {{ color: var(--accent-2); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  h1 {{ font-size: 1.5em; margin: 0 0 0.1em; letter-spacing: -0.01em; }}
  h2 {{ margin-top: 1.6em; font-size: 1.05em; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; color: var(--text); }}
  .meta {{ color: var(--muted); font-size: 0.85em; }}
  .muted {{ color: var(--muted); }}
  .small {{ font-size: 0.85em; }}
  code {{ background: var(--panel-2); padding: 1px 6px; border-radius: 3px; font-size: 0.92em; color: var(--accent-2); }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.4em 0; font-size: 12px; }}
  th, td {{ text-align: left; padding: 0.45em 0.6em; border-bottom: 1px solid var(--border); vertical-align: top; }}
  th {{ background: var(--panel); font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tr.row-self {{ background: rgba(99, 102, 241, 0.15); }}
  .pill {{ display: inline-block; padding: 2px 9px; border-radius: 10px; font-size: 0.75em; font-weight: 700; letter-spacing: 0.04em; }}
  .pill.ok {{ background: rgba(16, 185, 129, 0.18); color: var(--ok); }}
  .pill.bad {{ background: rgba(239, 68, 68, 0.18); color: var(--bad); }}
  .cat-pill {{ display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 0.72em; font-weight: 600;
               background: rgba(99, 102, 241, 0.16); color: #c7d2fe; }}
  pre {{ background: var(--panel); padding: 0.8em; border-radius: 6px; overflow: auto; font-size: 11px; border: 1px solid var(--border); color: var(--text); }}

  .summary {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 0.6em; margin: 0.8em 0 1.2em; }}
  .summary > div {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 0.7em 0.9em; }}
  .summary .label {{ font-size: 9px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.08em; }}
  .summary .value {{ font-size: 1.25em; font-weight: 700; margin-top: 0.2em; font-variant-numeric: tabular-nums; }}

  .variant-card {{ background: linear-gradient(135deg, var(--panel) 0%, var(--panel-2) 100%);
                    border: 1px solid var(--border); border-radius: 10px; padding: 1em 1.2em; margin: 0.5em 0 1.2em; }}
  .variant-card h3 {{ font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin: 0 0 0.5em; }}
  .variant-card .name {{ font-size: 1.15em; font-weight: 700; color: var(--accent-2); margin-bottom: 0.3em; }}
  .variant-card .desc {{ color: var(--text); font-size: 0.95em; line-height: 1.5; }}

  .pred-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.7em; }}
  .pred-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 0.7em 0.9em; }}
  .pred-head {{ display: flex; gap: 0.5em; align-items: center; font-size: 0.78em; color: var(--muted); margin-bottom: 0.4em; }}
  .pred-ts {{ font-variant-numeric: tabular-nums; }}
  .pred-ticker {{ font-size: 0.85em; margin-left: auto; }}
  .pred-title {{ font-weight: 600; color: var(--text); margin-bottom: 0.5em; font-size: 0.95em; line-height: 1.35; }}
  .pred-bars {{ margin: 0.4em 0; }}
  .prob-row {{ display: grid; grid-template-columns: minmax(80px, 1fr) 3fr 36px; gap: 0.5em; align-items: center; margin: 0.2em 0; font-size: 0.85em; }}
  .prob-label {{ color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .prob-bar {{ background: var(--panel-2); height: 14px; border-radius: 7px; overflow: hidden; }}
  .prob-fill {{ display: block; height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }}
  .prob-val {{ font-variant-numeric: tabular-nums; text-align: right; color: var(--text); font-weight: 600; }}
  .pred-rationale {{ color: var(--muted); font-size: 0.82em; line-height: 1.4; margin-top: 0.4em; padding-top: 0.4em; border-top: 1px dashed var(--border); }}
  .evidence {{ font-size: 0.78em; color: var(--muted); margin-top: 0.4em; }}
  .empty {{ text-align: center; color: var(--muted); padding: 2em; background: var(--panel); border-radius: 8px; border: 1px dashed var(--border); }}

  form.try {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 1em 1.2em; }}
  form.try label {{ display: block; font-size: 0.85em; color: var(--muted); margin: 0.6em 0 0.25em; }}
  form.try input, form.try textarea {{ width: 100%; background: var(--panel-2); color: var(--text); border: 1px solid var(--border); border-radius: 5px; padding: 0.45em 0.6em; font: inherit; }}
  form.try textarea {{ min-height: 60px; }}
  form.try button {{ background: var(--accent); color: white; border: 0; padding: 0.6em 1.2em; border-radius: 5px; font: inherit; font-weight: 600; margin-top: 0.8em; cursor: pointer; }}
  form.try button:hover {{ background: #4f46e5; }}
  #try-result {{ background: var(--panel-2); padding: 0.8em; border-radius: 6px; margin-top: 1em; font-family: ui-monospace, monospace; font-size: 11px; white-space: pre-wrap; word-break: break-all; }}

  @keyframes flash {{ 0% {{ background: rgba(56, 189, 248, 0.25); }} 100% {{ background: var(--panel); }} }}
  .pred-card.fresh {{ animation: flash 1.6s ease-out; }}
  .cat-chip {{ display: inline-block; background: var(--panel-2); border: 1px solid var(--border); padding: 3px 9px; border-radius: 12px; margin: 2px 4px 2px 0; font-size: 0.78em; }}
  .cat-chip strong {{ color: var(--accent-2); font-weight: 600; }}
  .arch-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 1em 1.2em; }}
  .arch-flow {{ display: flex; flex-wrap: wrap; gap: 0.4em; align-items: center; font-size: 0.85em; margin-top: 0.5em; }}
  .arch-step {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 5px; padding: 4px 9px; }}
  .arch-arrow {{ color: var(--muted); }}
  .live-pill {{ display: inline-block; padding: 2px 8px; border-radius: 8px; font-size: 0.72em; font-weight: 700;
                background: rgba(239, 68, 68, 0.18); color: #fca5a5; animation: pulse 2s infinite; }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.55; }} }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1em; align-items: start; }}
</style>
</head><body>

<h1>🔮 The Oracles — live dashboard <span class="live-pill" id="live-indicator">SSE LIVE</span></h1>
<p class="meta">team <strong>CanadaHacks</strong> · variant: <code>{html_escape(_VARIANT_NAME)}</code> · uptime <strong>{_uptime_human()}</strong> · auto-refresh 30s · streaming via <code>/events</code> ·
<a href="/docs">/docs</a> · <a href="/predictions">/predictions</a> · <a href="/healthz">/healthz</a></p>

<div class="summary">
  <div><div class="label">endpoint</div><div class="value">{endpoint_pill}</div></div>
  <div><div class="label">preds served</div><div class="value">{_TOTAL_PREDICTIONS}</div></div>
  <div><div class="label">api spend</div><div class="value">${_TOTAL_COST_USD:.3f}</div></div>
  <div><div class="label">avg p_yes ± 0.5</div><div class="value">{avg_p_dev:.2f}</div></div>
  <div><div class="label">last call</div><div class="value small">{html_escape(last_run[:19]) if last_run != '—' else '—'}</div></div>
  <div><div class="label">last status</div><div class="value">{html_escape(str(last_status))}</div></div>
</div>

<div class="grid-2">
  <div class="variant-card">
    <h3>Variant in production</h3>
    <div class="name">{html_escape(_VARIANT_NAME)}</div>
    <div class="desc">{html_escape(variant_desc)}</div>
    <div class="meta" style="margin-top:0.6em">cost ~${cost_per_event:.4f}/event · longshot guard floor: 0.05 or 0.5/n_outcomes (whichever is greater) · Kalshi paper compliance</div>
  </div>
  <div class="arch-card">
    <h3 style="font-size:0.85em;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin:0 0 0.5em">Architecture (multi_outcome_retrieval)</h3>
    <div class="arch-flow">
      <span class="arch-step">event JSON</span><span class="arch-arrow">→</span>
      <span class="arch-step">Brave Search (top 5)</span><span class="arch-arrow">→</span>
      <span class="arch-step">dedupe by domain priority</span><span class="arch-arrow">→</span>
      <span class="arch-step">enriched multi-outcome prompt</span><span class="arch-arrow">→</span>
      <span class="arch-step">Sonnet 4.6</span><span class="arch-arrow">→</span>
      <span class="arch-step">per-outcome probs</span><span class="arch-arrow">→</span>
      <span class="arch-step">Kalshi longshot guard</span><span class="arch-arrow">→</span>
      <span class="arch-step">{{"probabilities": [...]}}</span>
    </div>
    <div class="meta" style="margin-top:0.6em">priority domains: <code>.gov</code> · <code>.edu</code> · Kalshi · Polymarket · AP · Reuters · BBC · NPR · then anything</div>
  </div>
</div>

<div class="grid-2" style="margin-top:1em">
  <div class="arch-card">
    <h3 style="font-size:0.85em;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin:0 0 0.5em">Predictions/min (last 30 min)</h3>
    <div>{spark_svg}</div>
    <div class="meta small" style="margin-top:0.4em">total served: <strong>{_TOTAL_PREDICTIONS}</strong> · spend so far: <strong>${_TOTAL_COST_USD:.3f}</strong></div>
  </div>
  <div class="arch-card">
    <h3 style="font-size:0.85em;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin:0 0 0.5em">Categories in recent predictions</h3>
    <div>{cat_chips}</div>
    <div class="meta small" style="margin-top:0.5em">distribution shifts with the events Prophet Arena sends us</div>
  </div>
</div>

<h2>Per-variant Brier (lower is better) — from scripts/backtest_forecast.py</h2>
<div class="arch-card">
  {brier_svg}
  <div class="meta small" style="margin-top:0.4em">Brier here is the legacy single-p_yes metric from the local backtest. The proper per-outcome Brier (used for prize scoring) shows the same ranking with different absolute values. Note: <code>multi_outcome_retrieval</code>'s 0.064 is contaminated by data leakage (Brave finds articles about resolved past events); live performance on future events does not leak.</div>
</div>

<h2>Open events on Prophet Arena ({len(open_events_list)})</h2>
<table>
<thead><tr><th>market_ticker</th><th>category</th><th>title</th><th>close_time</th></tr></thead>
<tbody>{open_rows}</tbody>
</table>

<h2>Recent predictions served (<span id="pred-count">{len(_PREDICTION_HISTORY)}</span>)</h2>
<div class="pred-grid" id="pred-grid">{pred_cards}</div>

<h2>Leaderboard scores</h2>
{scores_block}

<h2>Try a prediction (live, hits production endpoint)</h2>
<form class="try" onsubmit="event.preventDefault(); doTry();">
  <label>title</label><input id="ti" value="Will the US Federal Reserve cut rates at the December 2026 meeting?">
  <label>category</label><input id="ca" value="Economics">
  <label>outcomes (comma-separated)</label><input id="ou" value="Yes, No">
  <label>close_time (ISO 8601)</label><input id="ct" value="2026-12-31T23:59:59Z">
  <button type="submit">Predict</button>
  <div id="try-result">Submit a question to see the live agent's per-outcome probabilities.</div>
</form>
<script>
async function doTry() {{
  const out = document.getElementById("try-result");
  out.textContent = "calling /predict ...";
  const outcomes = document.getElementById("ou").value.split(",").map(s => s.trim()).filter(Boolean);
  const body = {{
    event_ticker: "dashboard-try", market_ticker: "dashboard-try",
    title: document.getElementById("ti").value,
    category: document.getElementById("ca").value,
    close_time: document.getElementById("ct").value,
    outcomes
  }};
  try {{
    const r = await fetch("/predict", {{method: "POST", headers: {{"content-type": "application/json"}}, body: JSON.stringify(body)}});
    const j = await r.json();
    out.textContent = JSON.stringify(j, null, 2);
  }} catch (e) {{ out.textContent = "error: " + e.message; }}
}}

// SSE live feed: when a new prediction lands, prepend a flashing card.
(function initSSE() {{
  if (!window.EventSource) return;
  const indicator = document.getElementById("live-indicator");
  const es = new EventSource("/events");
  es.addEventListener("hello", (ev) => {{
    indicator.textContent = "SSE LIVE";
    indicator.style.background = "rgba(16, 185, 129, 0.2)";
    indicator.style.color = "#10b981";
  }});
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
      ? `<div class="evidence">📎 ${{ev_urls.map(u => {{ try {{ return `<a href="${{u}}" target="_blank">${{new URL(u).host}}</a>`; }} catch (_) {{ return ""; }} }}).join(" · ")}}</div>`
      : "";
    card.innerHTML = `
      <div class="pred-head"><span class="pred-ts">${{msg.ts.slice(11,19)}}</span><span class="cat-pill">${{msg.category||"?"}}</span><code class="pred-ticker">${{msg.market_ticker||"?"}}</code></div>
      <div class="pred-title">${{(msg.title||"").slice(0,140)}}</div>
      <div class="pred-bars">${{probsHtml}}</div>
      <div class="pred-rationale">${{(msg.rationale||"").slice(0,240)}}</div>
      ${{ev_html}}
    `;
    grid.insertBefore(card, grid.firstChild);
    while (grid.children.length > 20) grid.removeChild(grid.lastChild);
    document.getElementById("pred-count").textContent = msg.total_predictions;
  }});
  es.onerror = () => {{
    indicator.textContent = "SSE RECONNECTING";
    indicator.style.background = "rgba(245, 158, 11, 0.2)";
    indicator.style.color = "#fbbf24";
  }};
}})();
</script>

<h2>Quick links</h2>
<ul>
<li><a href="/healthz">/healthz</a> — server health JSON</li>
<li><a href="/docs">/docs</a> — Swagger UI</li>
<li><a href="/redoc">/redoc</a> — ReDoc</li>
<li><a href="/predictions">/predictions</a> — last 50 predictions JSON</li>
<li><a href="https://prophetarena.co/leaderboard/forecast">Prophet Arena leaderboard</a></li>
<li><a href="https://github.com/Robby955/prophet-hacks">GitHub repo</a></li>
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
