# prophet-hacks

A boring, reliable, observable agent for Prophet Arena. Survives the tick
loop, logs every decision as JSONL, and only trades when the forecast edge is
big enough.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.11+ (see `.python-version`; 3.13 is the dev baseline as of 2026-05-16).

## Environment variables

| Var | Required | Notes |
| --- | --- | --- |
| `PA_SERVER_API_KEY` | yes | Prophet Arena API key (sent as `X-API-Key`) |
| `PA_SERVER_URL` | no | Defaults to `https://api.aiprophet.dev` |
| `OPENAI_API_KEY` | for OpenAI variants | Used by triage model and ensemble |
| `ANTHROPIC_API_KEY` | for Anthropic variants | Used by forecast model |
| `PROPHET_FORECAST_MODEL` | no | Override forecast model. Default: `anthropic/claude-sonnet-4-6` |
| `PROPHET_TRIAGE_MODEL` | no | Override triage model. Default: `openai/gpt-5.4-mini` |
| `PA_N_TICKS` | no | Experiment-length hint. Default 96. |

Copy `.env.example` to `.env` (file not committed) and fill in.

## Run

### Continuous mode (default)

Runs ticks in a loop until the experiment completes or SIGINT/SIGTERM:

```bash
python agent.py --slug my-experiment --variant model-forecast-no-retrieval
```

### Single tick

Run exactly one tick and exit:

```bash
python agent.py --slug my-experiment --once
```

### Dry run

Smoke test without hitting the API:

```bash
python agent.py --slug smoke --dry-run
```

Resume is automatic: re-running with the same `--slug` resumes the same
experiment (the SDK call is `create_or_get_experiment`).

## Variants

Set in `config.yaml` or via `--variant`:

- `baseline-market-price` -- Uses market mid as `p_yes`. Trades nothing. Control.
- `model-forecast-no-retrieval` (default) -- Single LLM call. Question/description/quote only.
- `model-forecast-retrieval` (stub) -- Raises `NotImplementedError`.
- `calibrated-ensemble` -- Two-model agreement gate. Trades only when both models agree.

## Architecture

```
agent.py             tick lifecycle (BenchmarkSession) + CLI
forecaster.py        LLM callers, prompt, parsing, variant dispatch
market_filter.py     eligibility checks (sanity, freshness, headroom)
risk.py              hard caps and invariant assertions
logger.py            JSONL trace writer
config.yaml          model routing + policy thresholds
```

## Hard limits

All hard caps live in `risk.py` as module constants. `config.yaml` mirrors
them for visibility, but `risk.py` is authoritative. If they disagree,
the code wins.

| Cap | Value | Source |
| --- | --- | --- |
| `EDGE_THRESHOLD` | 0.08 | `risk.py` |
| `MAX_TRADES_PER_TICK` | 3 | `risk.py` (server allows 20) |
| `MAX_NOTIONAL_PER_NEW_POSITION` | $100 | `risk.py` |
| `MAX_NOTIONAL_PER_MARKET` | $1,000 | `risk.py` (matches server) |
| `MAX_OPEN_POSITIONS` | 30 | `risk.py` (matches server) |
| `MAX_MARKETS_ANALYZED_PER_TICK` | 5 | `risk.py` |

## Forecasting

The LLM forecaster (`model-forecast-no-retrieval`):
1. Constructs a structured prompt from market fields (question, description, topic, quotes, time to resolution)
2. Asks the model for `{"p_yes": float, "rationale": "..."}` with JSON mode
3. Parses with fallback (direct JSON, embedded JSON, regex)
4. Clamps to [0.01, 0.99] and rounds to nearest bucket
5. Computes edge vs market ask; trades only when edge >= 0.08

The ensemble variant (`calibrated-ensemble`):
1. Calls both triage (cheap) and forecast (strong) models
2. Applies agreement gate: trade only when both agree on direction AND both have |p - 0.5| >= 0.10
3. Returns averaged probability or falls back to market mid (skip)

## Trace output

One JSONL file per tick at `trace/<slug>/<tick_id>.jsonl`. One record per
market decision. Schema in `SUBMISSION_NOTES.md`.

## Tests

```bash
# With pytest (if available)
python -m pytest tests/ -v

# Without pytest
python -c "exec(open('tests/test_forecaster.py').read())"
```

## Submission

```bash
bash scripts/package_submission.sh
```
