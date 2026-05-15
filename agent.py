"""Prophet Arena tick agent.

Uses BenchmarkSession from ai-prophet-core for clean lifecycle management.

Lifecycle per tick:
    claim tick -> load candidates + portfolio -> forecast/skip each candidate
    -> convert high-edge forecasts to intents -> submit -> finalize
    -> complete tick -> write JSONL trace

Run modes:
    --dry-run           Parse config and exit. No API calls.
    --once              Run exactly one tick and exit.
    (default)           Continuous loop: run ticks until experiment completes.

Auth env vars:
    PA_SERVER_URL       (optional; defaults to https://api.aiprophet.dev)
    PA_SERVER_API_KEY   (required for any write call against the live API)
    OPENAI_API_KEY      (required for openai model variants)
    ANTHROPIC_API_KEY   (required for anthropic model variants)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

import forecaster
import market_filter
import risk
from logger import TraceWriter, append_experiment_row, build_decision_record


log = logging.getLogger("prophet-hacks")

# Graceful shutdown flag
_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    log.info("received signal %s, shutting down after current tick", signum)
    _shutdown = True


def _config_hash(config: dict) -> str:
    payload = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _build_client():
    """Build the ServerAPIClient from env vars."""
    from ai_prophet_core import DEFAULT_API_URL, ServerAPIClient
    base = os.getenv("PA_SERVER_URL", DEFAULT_API_URL)
    api_key = os.getenv("PA_SERVER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "PA_SERVER_API_KEY not set. Copy .env.example to .env and fill in."
        )
    return ServerAPIClient(base_url=base, api_key=api_key, timeout=30)


def _build_session():
    """Build a BenchmarkSession wrapping the API client."""
    from ai_prophet_core.arena import BenchmarkSession
    client = _build_client()
    return BenchmarkSession(client)


def _build_intents(
    *,
    market_id: str,
    side: str,
    size: int,
    tick_id: str,
) -> list:
    """Wrap one trade into the SDK's intent type."""
    from ai_prophet_core import TradeIntentRequest
    idem = hashlib.sha256(
        f"{tick_id}|{market_id}|{side}|{size}".encode("utf-8"),
    ).hexdigest()[:24]
    return [
        TradeIntentRequest(
            market_id=market_id,
            action="BUY",
            side=side.upper(),
            shares=str(size),
            idempotency_key=idem,
        ),
    ]


def _decide_for_market(market, p_yes: float):
    """Return (action, side, size, notional, skip_reason, edges).

    Pure function: no SDK calls. Risk checks against the open-position list
    are applied separately at the caller.
    """
    yes_ask = float(market.quote.best_ask)
    bid = float(market.quote.best_bid)
    no_ask = 1.0 - bid
    y_edge = forecaster.yes_edge(p_yes, yes_ask)
    n_edge = forecaster.no_edge(p_yes, no_ask)

    if y_edge >= risk.EDGE_THRESHOLD and y_edge >= n_edge:
        size = risk.position_size_for_notional(
            risk.MAX_NOTIONAL_PER_NEW_POSITION, yes_ask,
        )
        if size <= 0:
            return ("SKIP", None, 0, 0.0, "size rounds to 0", (y_edge, n_edge))
        notional = size * yes_ask
        return ("BUY", "YES", size, notional, None, (y_edge, n_edge))

    if n_edge >= risk.EDGE_THRESHOLD:
        size = risk.position_size_for_notional(
            risk.MAX_NOTIONAL_PER_NEW_POSITION, no_ask,
        )
        if size <= 0:
            return ("SKIP", None, 0, 0.0, "size rounds to 0", (y_edge, n_edge))
        notional = size * no_ask
        return ("BUY", "NO", size, notional, None, (y_edge, n_edge))

    return (
        "SKIP",
        None,
        0,
        0.0,
        f"edge below threshold (yes={y_edge:.3f}, no={n_edge:.3f}, "
        f"threshold={risk.EDGE_THRESHOLD})",
        (y_edge, n_edge),
    )


def run_one_tick(
    *,
    session,
    lease,
    participant_idx: int,
    config: dict,
    slug: str,
    variant: str,
) -> dict:
    """One full tick: load -> forecast -> submit -> finalize -> complete.

    The session has already claimed the tick (lease is valid).
    Returns a small summary dict for stdout.
    """
    config_hash = _config_hash(config)
    trace = TraceWriter(
        trace_dir=config["runtime"]["trace_dir"],
        experiment_slug=slug,
        tick_id=lease.tick_id,
    )

    # Load candidates + portfolio
    tick = session.load_candidates(lease)
    lease = tick.lease  # Updated lease with candidate_set_id
    candidates = tick.candidates

    portfolio = session.get_portfolio(participant_idx=participant_idx)
    positions = portfolio.positions if portfolio else []

    # Filter and cap
    eligible = market_filter.filter_candidates(candidates.markets, positions)
    eligible = eligible[: risk.MAX_MARKETS_ANALYZED_PER_TICK]

    log.info(
        "tick %s: %d candidates, %d eligible (capped at %d)",
        lease.tick_id, len(candidates.markets), len(eligible),
        risk.MAX_MARKETS_ANALYZED_PER_TICK,
    )

    # Forecast and build intents
    from ai_prophet_core import TradeIntentRequest
    intents: list[TradeIntentRequest] = []
    trades_this_tick = 0
    total_cost = 0.0

    for m in eligible:
        try:
            fcast = forecaster.forecast(m, variant=variant)
        except NotImplementedError as e:
            trace.write(build_decision_record(
                tick_id=lease.tick_id, market_id=m.market_id, question=m.question,
                bid=float(m.quote.best_bid), ask=float(m.quote.best_ask),
                action="SKIP", skip_reason=f"variant not wired: {e}",
                config_hash=config_hash,
            ))
            continue

        p_yes = fcast["p_yes"]
        action, side, size, notional, skip_reason, (y_edge, n_edge) = _decide_for_market(m, p_yes)
        total_cost += fcast.get("cost_estimate_usd", 0.0)

        # Risk gate
        if action == "BUY":
            try:
                risk.assert_under_trades_per_tick(trades_this_tick)
                risk.assert_no_conflicting_position(m.market_id, side, positions)
                risk.assert_under_position_count(len(positions))
                current_market_notional = sum(
                    float(p.shares) * float(p.avg_entry_price)
                    for p in positions
                    if p.market_id == m.market_id
                )
                risk.assert_under_notional_cap(notional, current_market_notional)
            except risk.RiskViolation as rv:
                action, side, size, notional, skip_reason = "SKIP", None, 0, 0.0, str(rv)

        if action == "BUY":
            intents.extend(_build_intents(
                market_id=m.market_id, side=side, size=size, tick_id=lease.tick_id,
            ))
            trades_this_tick += 1

        trace.write(build_decision_record(
            tick_id=lease.tick_id, market_id=m.market_id, question=m.question,
            bid=float(m.quote.best_bid), ask=float(m.quote.best_ask),
            model_provider=fcast["model_provider"], model=fcast["model"],
            p_yes=p_yes, probability_bucket=fcast["probability_bucket"],
            implied_market_probability=forecaster.implied_market_probability(m),
            yes_edge=y_edge, no_edge=n_edge,
            action=action, side=side, size=size, notional=notional,
            skip_reason=skip_reason,
            prompt_hash=fcast["prompt_hash"], config_hash=config_hash,
            cost_estimate_usd=fcast["cost_estimate_usd"],
            evidence_urls=fcast.get("evidence", []),
            notes=fcast.get("confidence_note", ""),
        ))

    # Submit intents
    submission = None
    if intents:
        submission = session.submit_intents(
            lease, participant_idx=participant_idx, intents=intents,
        )
        log.info(
            "tick %s: submitted %d intents, accepted=%d rejected=%d",
            lease.tick_id, len(intents),
            submission.accepted, submission.rejected,
        )
    else:
        log.info("tick %s: no intents to submit (all skipped)", lease.tick_id)

    # Finalize and complete
    session.finalize(lease, participant_idx=participant_idx)
    session.complete_tick(lease)

    # Log to experiment log
    append_experiment_row(
        slug=slug, variant=variant, config_hash=config_hash, tick_count=1,
        outcome="ok",
        notes=(
            f"candidates={len(candidates.markets)} eligible={len(eligible)} "
            f"intents={len(intents)} "
            f"accepted={submission.accepted if submission else 0} "
            f"rejected={submission.rejected if submission else 0} "
            f"cost=${total_cost:.4f}"
        ),
    )

    return {
        "status": "ok",
        "tick_id": lease.tick_id,
        "candidates_seen": len(candidates.markets),
        "eligible": len(eligible),
        "intents_submitted": len(intents),
        "accepted": submission.accepted if submission else 0,
        "rejected": submission.rejected if submission else 0,
        "total_cost_usd": round(total_cost, 4),
    }


def run_continuous(
    *,
    session,
    config: dict,
    slug: str,
    variant: str,
    model_tag: str,
    once: bool = False,
) -> int:
    """Main tick loop. Runs until experiment completes or shutdown signal."""
    config_hash = _config_hash(config)
    n_ticks_param = int(os.getenv("PA_N_TICKS", "96"))

    # Create or resume experiment
    exp = session.create_experiment(
        slug=slug,
        config_hash=config_hash,
        config_json=config,
        n_ticks=n_ticks_param,
    )
    log.info("experiment %s (slug=%s)", exp.experiment_id, slug)

    # Register participant
    part = session.upsert_participant(
        model=model_tag,
        starting_cash=float(risk.STARTING_BANKROLL),
    )
    participant_idx = part.participant_idx
    log.info("participant idx=%d model=%s", participant_idx, model_tag)

    tick_count = 0
    while not _shutdown:
        # Claim the next tick
        lease = session.claim_tick()

        if not lease.available:
            if lease.reason == "experiment_completed":
                log.info("experiment completed after %d ticks", tick_count)
                return 0

            wait = lease.retry_after_sec or 15
            log.info(
                "no tick available (reason=%s), waiting %ds",
                lease.reason, wait,
            )

            if once:
                log.info("--once mode: no tick available, exiting")
                return 0

            time.sleep(wait)
            continue

        # Run the tick
        try:
            summary = run_one_tick(
                session=session,
                lease=lease,
                participant_idx=participant_idx,
                config=config,
                slug=slug,
                variant=variant,
            )
            tick_count += 1
            log.info(
                "tick %d complete: %s",
                tick_count, json.dumps(summary, default=str),
            )
        except Exception:
            log.exception("tick failed (lease=%s)", lease.tick_id)
            # Finalize as FAILED so the experiment can advance
            try:
                session.finalize(
                    lease, participant_idx=participant_idx,
                    status="FAILED",
                    error_code="TICK_ERROR",
                )
                session.complete_tick(lease)
            except Exception:
                log.exception("failed to finalize/complete after error")

        if once:
            return 0

        # Brief pause before claiming next tick
        time.sleep(2)

    log.info("shutdown requested after %d ticks", tick_count)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prophet Arena tick agent (boring + observable).",
    )
    parser.add_argument(
        "--slug", required=True,
        help="experiment slug (resumes if exists)",
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="path to config.yaml",
    )
    parser.add_argument(
        "--variant", default=None,
        help="override variant from config (e.g. model-forecast-no-retrieval)",
    )
    parser.add_argument(
        "--model-tag", default="rob-baseline-v0",
        help="participant model tag, recorded server-side",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="load config + parse args; do not call the API",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="run exactly one tick and exit",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    config = load_config(args.config)
    config.setdefault("runtime", {})
    config["runtime"]["experiment_slug"] = args.slug
    variant = args.variant or config.get("variant", "baseline-market-price")

    Path(config["runtime"].get("log_dir", "logs")).mkdir(parents=True, exist_ok=True)
    Path(config["runtime"].get("trace_dir", "trace")).mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        log.info("dry-run: slug=%s variant=%s", args.slug, variant)
        log.info("config hash: %s", _config_hash(config))
        log.info("hard caps: EDGE=%s MAX_TRADES=%s MAX_NOTIONAL=%s",
                 risk.EDGE_THRESHOLD, risk.MAX_TRADES_PER_TICK,
                 risk.MAX_NOTIONAL_PER_NEW_POSITION)
        log.info("forecast model: %s", os.getenv("PROPHET_FORECAST_MODEL", "anthropic/claude-sonnet-4-6"))
        log.info("triage model: %s", os.getenv("PROPHET_TRIAGE_MODEL", "openai/gpt-5.4-mini"))
        # Verify we can import the SDK
        from ai_prophet_core import ServerAPIClient, TradeIntentRequest
        log.info("SDK imports ok: ServerAPIClient, TradeIntentRequest")
        return 0

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    session = _build_session()
    try:
        return run_continuous(
            session=session,
            config=config,
            slug=args.slug,
            variant=variant,
            model_tag=args.model_tag,
            once=args.once,
        )
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
