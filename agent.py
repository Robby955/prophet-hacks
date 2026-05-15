"""Prophet Arena tick agent.

SDK surface (ai-prophet-core 0.1.4, module path ai_prophet_core.client.ServerAPIClient):
    create_or_get_experiment(slug, config_hash, config_json, n_ticks)
    upsert_participant(experiment_id, model, rep, starting_cash)
    claim_tick(experiment_id, lease_owner_id, lease_sec)
    get_candidates(tick_ts, candidate_set_id)
    get_portfolio(experiment_id, participant_idx)
    put_plan(experiment_id, participant_idx, tick_id, candidate_set_id, plan_json)
    submit_trade_intents(experiment_id, participant_idx, tick_id,
                         candidate_set_id, intents)
    finalize_participant(experiment_id, participant_idx, tick_id, status,
                         error_code, error_detail)
    complete_tick(experiment_id, tick_id)
    complete_experiment(experiment_id)

Lifecycle (Rob's spec, mapped to SDK names):
    create/resume experiment       -> create_or_get_experiment
    claim tick                     -> claim_tick
    load candidates + portfolio    -> get_candidates + get_portfolio
    forecast/skip each candidate   -> forecaster.forecast(...)
    convert high-edge -> intents   -> _build_intents
    submit                         -> submit_trade_intents
    finalize                       -> finalize_participant
    complete tick                  -> complete_tick
    write JSONL trace              -> logger.TraceWriter (per-decision)

Auth env vars (from ai_prophet/trade/core/credentials.py):
    PA_SERVER_URL       (optional; defaults to https://api.aiprophet.dev)
    PA_SERVER_API_KEY   (required for any write call against the live API)
    ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY (per-provider, for
    LLM variants once enabled)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import socket
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

import forecaster
import market_filter
import risk
from logger import TraceWriter, append_experiment_row, build_decision_record


log = logging.getLogger("prophet-hacks")


def _config_hash(config: dict) -> str:
    payload = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _lease_owner_id() -> str:
    host = socket.gethostname()
    pid = os.getpid()
    return f"{host}-{pid}-{uuid.uuid4().hex[:8]}"


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _build_client():
    """Lazy import so smoke tests work without network deps installed."""
    from ai_prophet_core import DEFAULT_API_URL, ServerAPIClient
    base = os.getenv("PA_SERVER_URL", DEFAULT_API_URL)
    api_key = os.getenv("PA_SERVER_API_KEY")
    return ServerAPIClient(base_url=base, api_key=api_key)


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


def run_one_tick(*, client, config: dict, slug: str, variant: str, model_tag: str) -> dict:
    """One full tick: claim -> load -> forecast -> submit -> finalize -> complete.

    Returns a small summary dict for stdout. JSONL records are written by
    the per-decision TraceWriter regardless of return path.
    """
    config_hash = _config_hash(config)
    n_ticks_param = int(os.getenv("PA_N_TICKS", "96"))

    exp = client.create_or_get_experiment(
        slug=slug,
        config_hash=config_hash,
        config_json=config,
        n_ticks=n_ticks_param,
    )
    participant = client.upsert_participant(
        experiment_id=exp.experiment_id,
        model=model_tag,
        rep=0,
        starting_cash=float(risk.STARTING_BANKROLL),
    )

    claim = client.claim_tick(
        experiment_id=exp.experiment_id,
        lease_owner_id=_lease_owner_id(),
        lease_sec=600,
    )
    if claim.no_tick_available:
        return {
            "status": "no_tick_available",
            "retry_after_sec": claim.retry_after_sec,
            "reason": claim.reason,
        }

    tick_id = claim.tick_id
    candidate_set_id = claim.snapshot_id
    trace = TraceWriter(
        trace_dir=config["runtime"]["trace_dir"],
        experiment_slug=slug,
        tick_id=tick_id,
    )

    candidates = client.get_candidates(
        tick_ts=claim.tick_ts,
        candidate_set_id=candidate_set_id,
    )
    portfolio = client.get_portfolio(
        experiment_id=exp.experiment_id,
        participant_idx=participant.participant_idx,
    )
    positions = portfolio.positions if portfolio else []

    eligible = market_filter.filter_candidates(candidates.markets, positions)
    eligible = eligible[: risk.MAX_MARKETS_ANALYZED_PER_TICK]

    intents = []
    trades_this_tick = 0
    for m in eligible:
        try:
            fcast = forecaster.forecast(m, variant=variant)
        except NotImplementedError as e:
            trace.write(build_decision_record(
                tick_id=tick_id, market_id=m.market_id, question=m.question,
                bid=float(m.quote.best_bid), ask=float(m.quote.best_ask),
                action="SKIP", skip_reason=f"variant not wired: {e}",
                config_hash=config_hash,
            ))
            continue

        p_yes = fcast["p_yes"]
        action, side, size, notional, skip_reason, (y_edge, n_edge) = _decide_for_market(m, p_yes)

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
                market_id=m.market_id, side=side, size=size, tick_id=tick_id,
            ))
            trades_this_tick += 1

        trace.write(build_decision_record(
            tick_id=tick_id, market_id=m.market_id, question=m.question,
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

    submission = None
    if intents:
        submission = client.submit_trade_intents(
            experiment_id=exp.experiment_id,
            participant_idx=participant.participant_idx,
            tick_id=tick_id,
            candidate_set_id=candidate_set_id,
            intents=intents,
        )

    client.finalize_participant(
        experiment_id=exp.experiment_id,
        participant_idx=participant.participant_idx,
        tick_id=tick_id,
        status="COMPLETED",
    )
    client.complete_tick(experiment_id=exp.experiment_id, tick_id=tick_id)

    append_experiment_row(
        slug=slug, variant=variant, config_hash=config_hash, tick_count=1,
        outcome="ok",
        notes=(
            f"candidates={candidates.market_count} eligible={len(eligible)} "
            f"intents={len(intents)} "
            f"accepted={submission.accepted if submission else 0} "
            f"rejected={submission.rejected if submission else 0}"
        ),
    )

    return {
        "status": "ok",
        "experiment_id": exp.experiment_id,
        "tick_id": tick_id,
        "candidates_seen": candidates.market_count,
        "eligible": len(eligible),
        "intents_submitted": len(intents),
        "accepted": submission.accepted if submission else 0,
        "rejected": submission.rejected if submission else 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prophet Arena tick agent (boring + observable).")
    parser.add_argument("--slug", required=True, help="experiment slug (resumes if exists)")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--variant", default=None,
                        help="override variant from config (e.g. baseline-market-price)")
    parser.add_argument("--model-tag", default="rob-baseline-v0",
                        help="participant model tag, recorded server-side")
    parser.add_argument("--dry-run", action="store_true",
                        help="load config + parse args; do not call the API")
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
        return 0

    client = _build_client()
    try:
        summary = run_one_tick(
            client=client, config=config, slug=args.slug,
            variant=variant, model_tag=args.model_tag,
        )
    finally:
        client.close()
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary.get("status") in {"ok", "no_tick_available"} else 1


if __name__ == "__main__":
    sys.exit(main())
