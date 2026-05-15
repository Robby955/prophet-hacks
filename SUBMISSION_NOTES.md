# SUBMISSION_NOTES

Author: Rob Sneiderman. Prophet Hacks, 2026-05-16/17.

## What this agent is

A single-process tick agent for Prophet Arena. One tick is one full
lifecycle (claim, load, forecast, submit, finalize, complete). No
multi-agent committee. No retrieval in the default config. Every decision
writes a JSONL record before the next decision starts, so a crash leaves a
recoverable trail.

## Calibration table

The same calibration target used in the SDK's example agent. Forecasts
outside [0.10, 0.90] need stronger justification than the prompt itself.

| Probability bucket | Meaning |
| --- | --- |
| 0.50 | No view. Treat as the market's prior. Default when the forecaster has nothing to say. |
| 0.60 | Slight lean. Background reading or weak base rate suggests a tilt; not enough to override the market by much. |
| 0.70 | Real view. Concrete reasoning, multiple consistent signals, and a coherent story for why the market is mispriced. |
| 0.80 | Strong view. Hard evidence and a clear mechanism. Used sparingly. |
| 0.90 | Near-certain. Mechanically determined or covered by an authoritative source. Used very sparingly. |

`P_YES_MIN = 0.01` and `P_YES_MAX = 0.99`. We never emit 0.00 or 1.00.

## Policy decisions and rationale

- `EDGE_THRESHOLD = 0.08`. Trades fire only when `p_yes - yes_ask >= 0.08`
  (or the NO-side analogue). Below that we are paying the spread without
  enough cushion against being wrong.
- `MAX_MARKETS_ANALYZED_PER_TICK = 5`. Bounds model spend per tick.
- `MAX_TRADES_PER_TICK = 3`. Caps concentration risk inside a single tick.
- `MAX_NOTIONAL_PER_NEW_POSITION = 100` USD. Caps single-trade blast radius.
- `MAX_NOTIONAL_PER_MARKET = 1000` USD. Mirrors the official rules.
- `MAX_OPEN_POSITIONS = 30`. Bounds total portfolio breadth.
- Skip-by-default. Any uncertainty in filters, sizing, or risk checks falls
  through to a SKIP record. The agent should leave more on the table than
  it gives back to bad fills.
- Never YES and NO on the same market. Asserted in `risk.assert_no_conflicting_position`.

## What is NOT implemented in the skeleton

- LLM-backed forecast variants (`model-forecast-no-retrieval`,
  `model-forecast-retrieval`, `calibrated-ensemble`). They raise
  `NotImplementedError` with a pointer to the plug-in site.
- Retrieval. `policy.retrieval_enabled = false` in `config.yaml`.
- Multi-tick scheduling (cron, supervisor). The agent runs one tick per
  invocation. A simple `while true; sleep <TICK_INTERVAL_MIN>m; agent.py`
  loop is the intended runtime, with `--slug` constant to resume the same
  experiment.

## Operational risk and how the skeleton handles it

- **Idempotency on retries.** Each `TradeIntentRequest` carries an
  `idempotency_key` derived from `sha256(tick_id|market_id|side|size)`.
  Re-submitting the same intents in the same tick is a no-op server-side.
- **Partial fills.** The SDK returns `TradeSubmissionResult` with `fills`
  and `rejections` lists. The summary printed at end-of-tick reports
  `accepted` and `rejected` counts. Inspect the trace JSONL for details.
- **API timeouts.** `ServerAPIClient` retries with jittered exponential
  backoff (`max_retries=3`, `retry_backoff=1.0`) and honors `Retry-After`.
  On unrecoverable failure the agent propagates the exception and the
  current lease will expire server-side.
- **Stale quotes.** `market_filter.check_quote_freshness` drops any market
  whose quote ts is older than `STALE_AFTER_MIN = 30` minutes.
- **Crash mid-tick.** The trace JSONL is written record-by-record. If the
  process dies before `complete_tick`, the lease expires and the next run
  can re-claim the same tick. Decisions written before the crash remain on
  disk.

## JSONL schema (one record per market decision)

```json
{
  "timestamp": "...",
  "tick_id": "...",
  "market_id": "...",
  "question": "...",
  "bid": 0.41,
  "ask": 0.45,
  "mid": 0.43,
  "current_position": null,
  "model_provider": "openai",
  "model": "...",
  "p_yes": 0.70,
  "probability_bucket": 0.70,
  "implied_market_probability": 0.45,
  "yes_edge": 0.25,
  "no_edge": -0.33,
  "action": "BUY",
  "side": "YES",
  "size": 10,
  "notional": 45.0,
  "skip_reason": null,
  "prompt_hash": "...",
  "config_hash": "...",
  "cost_estimate_usd": 0.012,
  "evidence_urls": [],
  "notes": "..."
}
```

Written to `trace/{experiment_slug}/{tick_id}.jsonl`. One record per decision,
appended in order. The file name uses a colon-sanitized tick_id so it works
on case-insensitive filesystems.

## SDK function-name mapping (Rob's spec vs ai-prophet-core 0.1.4)

| Rob's name | Actual SDK method |
| --- | --- |
| create/resume experiment | `create_or_get_experiment` |
| claim tick | `claim_tick` |
| load candidates | `get_candidates` |
| load portfolio | `get_portfolio` |
| submit intents | `submit_trade_intents` |
| finalize | `finalize_participant` |
| complete tick | `complete_tick` |

Auth env var is `PA_SERVER_API_KEY` (not `PROPHET_API_KEY`). Server URL is
`PA_SERVER_URL` and defaults to `https://api.aiprophet.dev`. The brief's
"PROPHET_API_KEY" placeholder is corrected to `PA_SERVER_API_KEY` in
`README.md` and `agent.py`.

## Submission gates checklist

- [ ] `python agent.py --slug smoke --dry-run` exits 0
- [ ] `python -c "import agent, risk, forecaster, market_filter, logger"` succeeds
- [ ] `.env` is gitignored and contains no committed secrets
- [ ] `risk.py` constants match `config.yaml` policy section
- [ ] One full live tick has been observed end-to-end on a test slug
- [ ] Trace JSONL contains the expected schema for that tick
- [ ] Submission zip excludes `.venv`, `logs`, `trace`, `.env`, `.git`
