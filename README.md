# prophet-hacks

A boring, reliable, observable agent for Prophet Arena. Survives the tick
loop, logs every decision as JSONL, and only trades when the forecast edge is
big enough.

This is the skeleton. The default `baseline-market-price` variant uses the
market mid as the forecast, so by construction the edge is always 0 and no
trades fire. It is wired end-to-end (claim, candidates, portfolio, finalize,
complete) so we can prove the plumbing on a real tick before plugging an LLM
into `forecaster.py`.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.11 (see `.python-version`).

## Environment variables

| Var | Required | Notes |
| --- | --- | --- |
| `PA_SERVER_API_KEY` | yes | Prophet Arena API key (sent as `X-API-Key`) |
| `PA_SERVER_URL` | no | Defaults to `https://api.aiprophet.dev` |
| `ANTHROPIC_API_KEY` | only for LLM variants | Used by `forecast_model_no_retrieval` once wired |
| `OPENAI_API_KEY` | only for LLM variants | Same |
| `PA_N_TICKS` | no | Experiment-length hint for `create_or_get_experiment`. Default 96. |

Copy `.env.example` to `.env` (file not committed) and fill in.

## Run

```bash
python agent.py --slug my-experiment --variant baseline-market-price
```

Resume is automatic: re-running with the same `--slug` resumes the same
experiment (the SDK call is `create_or_get_experiment`).

Smoke test without hitting the API:

```bash
python agent.py --slug smoke --dry-run
```

## Variants

Set in `config.yaml` or via `--variant`:

- `baseline-market-price` (wired). Uses market mid as `p_yes`. Trades nothing.
- `model-forecast-no-retrieval` (stub). Raises `NotImplementedError` pointing at the plug-in site.
- `model-forecast-retrieval` (stub). Same.
- `calibrated-ensemble` (stub). Same.

## Hard limits

All hard caps live in `risk.py` as module constants. `config.yaml` mirrors
them for visibility, but `risk.py` is authoritative. If they disagree,
the code wins.

## Trace output

One JSONL file per tick at `trace/<slug>/<tick_id>.jsonl`. One record per
market decision. Schema reproduced in `SUBMISSION_NOTES.md`.

## Submission package

When ready to submit, zip only source and notes:

```bash
zip -r prophet-hacks-submission.zip . \
  -x ".venv/*" "__pycache__/*" "*.pyc" \
     "logs/*" "trace/*" ".env" ".env.*" \
     ".git/*" ".pytest_cache/*" ".mypy_cache/*" ".ruff_cache/*"
```

## Repo layout

```
agent.py             tick lifecycle and CLI
forecaster.py        variant dispatch and p_yes computation
market_filter.py     eligibility checks
risk.py              hard caps and invariant assertions
logger.py            JSONL trace writer
config.yaml          model routing + policy thresholds (mirror)
requirements.txt     pinned packages
.python-version      3.11
logs/                runtime logs (gitignored contents)
trace/               per-tick JSONL traces (gitignored contents)
README.md
SUBMISSION_NOTES.md
```
