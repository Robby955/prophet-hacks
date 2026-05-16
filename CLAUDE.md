# prophet-hacks

A single-process tick agent for Prophet Arena. One tick is one full lifecycle
(claim, load, forecast, submit, finalize, complete). Every decision writes
a JSONL record before the next decision starts so a crash leaves a
recoverable trail. Skeleton chosen to be boring and observable; calibration
and risk discipline are the things being showcased.

The repo doubles as a quant-portfolio piece. Treat `docs/DECISIONS.md`,
the JSONL traces, and the retrospective as P0 outputs, not afterthoughts.

## Event window

- **Prophet Hacks**, Chicago. Kickoff **Sat 2026-05-16 09:00 CT**, runs through Sunday.
- `docs/PRE_EVENT_CHECKLIST.md` is the run-this-before-kickoff list.
- `docs/RUNBOOK.md` is the run-this-when-things-break list.
- `docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md` gets filled in within 7 days of close.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # ai-prophet-core==0.1.4 pinned
cp .env.example .env              # fill in PA_SERVER_API_KEY + provider keys
python agent.py --slug smoke --dry-run        # no API calls
python agent.py --slug <slug> --variant <v>   # continuous loop
python agent.py --slug <slug> --once          # one tick and exit
```

Resume is automatic — re-running with the same `--slug` calls
`create_or_get_experiment` server-side.

## Architecture

```
agent.py             tick lifecycle (BenchmarkSession) + CLI + signal handlers
forecaster.py        variant dispatch, prompt, LLM callers, agreement gate
market_filter.py     eligibility checks (sanity, freshness, headroom, conflicts)
risk.py              hard caps + invariant asserts (authoritative)
logger.py            JSONL trace writer + experiment-log appender
config.yaml          model routing + policy thresholds (mirrors risk.py for visibility)
```

`risk.py` constants win if they ever disagree with `config.yaml`.

## Variants

Select with `--variant`:

- `baseline-market-price` — market mid as `p_yes`. Control. Never trades.
- `model-forecast-no-retrieval` (default) — single LLM call on question/description/quote.
- `model-forecast-retrieval` — stub, raises `NotImplementedError`. Codex Goal 2.
- `calibrated-ensemble` — triage + forecast models; trade only when both agree on direction AND `|p - 0.5| >= 0.10` for both.

## Models

Read from `config.yaml` (or `PROPHET_FORECAST_MODEL` / `PROPHET_TRIAGE_MODEL` env):

- Forecast: `anthropic/claude-sonnet-4-6` (default). Fallback chain: Haiku 4.5 → `gpt-5.4-mini`.
- Triage: `openai/gpt-5.4-mini`. **No `gpt-5.5-mini` exists** — the OpenAI mini tier tops out at `gpt-5.4-mini`. See `docs/DECISIONS.md` 2026-05-15 entry.

## Hard caps (live in `risk.py`)

| Constant | Value | Note |
| --- | --- | --- |
| `EDGE_THRESHOLD` | 0.08 | YES-edge or NO-edge must clear this |
| `MAX_TRADES_PER_TICK` | 3 | server allows 20 |
| `MAX_MARKETS_ANALYZED_PER_TICK` | 5 | bounds model spend |
| `MAX_NOTIONAL_PER_NEW_POSITION` | $100 | per-trade blast radius |
| `MAX_NOTIONAL_PER_MARKET` | $1,000 | matches server |
| `MAX_OPEN_POSITIONS` | 30 | matches server |
| `P_YES_MIN` / `P_YES_MAX` | 0.01 / 0.99 | never emit 0/1 |

Probability forecasts snap to `BUCKETS = [0.10, 0.20, …, 0.90]`. Calibration
table tying each bucket to a verbal meaning is in `SUBMISSION_NOTES.md`.

## Trace schema

One JSONL file per tick at `trace/<slug>/<tick_id>.jsonl`, one record per
market decision. 21 fields — schema in `SUBMISSION_NOTES.md`. The
`build_decision_record` helper in `logger.py` is the canonical constructor.

## Secrets

API keys live in **`~/Desktop/variables.txt`** (outside the repo). Copy
the lines you need into `.env`. `.env` is gitignored — never commit it.
`PA_SERVER_API_KEY` comes from the kickoff materials Saturday morning; the
provider keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) are already in
`variables.txt`.

## Multi-agent coordination

Read `docs/WORKTREE_PROTOCOL.md` before opening a second agent on this repo.
Summary:

- Non-Rob agents work in `.claude/worktrees/<descriptor>/` on `feat/<dated-slug>` branches. Never touch `main` directly.
- `agent.py` and `config.yaml` are owned by one agent per session. Claim them in `docs/AGENT_STATUS.md` before editing. Other agents touch `forecaster.py`, `market_filter.py`, `risk.py`, `retrieval/`, `prompts/` instead.
- Codex has five durable Goals in `docs/CODEX_GOALS.md`. Each ends with `Verified by ./scripts/agent/verify.sh passing.` Stop signals are `pass`, `block: <reason>`, `no-change-needed`.

## Commits

- Convention: `<scope>(<area>): <one-line>`, e.g. `feat(forecaster): wire opus-4-7 single-call variant`.
- **Never include AI co-author trailers** (`Co-Authored-By: Claude …`, `🤖 Generated with Claude Code`). This repo will be flipped public post-event; commit history should read as Rob's work.
- Never `git commit --amend` a pushed commit. New commits only.
- `git config --local user.email` must equal `robbysneiderman@gmail.com`.

## Verify gate

`./scripts/agent/verify.sh` is the gate every Codex Goal ends with. It runs:

1. `mypy --strict` on the five core modules (skips with a warning if mypy isn't configured).
2. `pytest tests/` (skips if no tests).
3. Smoke import: `python -c "import agent, risk, forecaster, market_filter, logger"`.
4. `python agent.py --slug verify-smoke --dry-run`.

Treat verify.sh green as the merge signal, not a hand-wave.

## RunPod

**OFF by default.** The tick loop is CPU/IO bound; forecasting goes to
frontier APIs over HTTPS. Four scenarios in `docs/RUNPOD_POSTURE.md` can
flip it on (OSS hosting if frontier spend > $200/10d, embedding service if
retrieval lands at volume, fine-tuning if historical labels expose, niche
OSS forecasting model). Triggering one is Rob's call. If an agent thinks a
trigger fires, escalate via `docs/AGENT_STATUS.md`, don't spin up
infrastructure unilaterally.

## Untracked at repo root (not yet committed)

- `forecasting_agent_monitoring_playbook.pdf`, `prophet_hacks_research_monitoring_playbook_v4.pdf` — reference PDFs.
- `forecasting_monitoring_demo/` — toy offline monitoring harness (Kalshi-style longshot guard, Brier/ECE summary, decision-funnel plot). Useful as a sanity-check shape before live ticks are flowing.
