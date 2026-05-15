# Pre-event checklist

Run this Saturday 2026-05-16 before 09:00 Chicago. Step through top to
bottom. Anything that fails: do not skip it; fix the laptop now, not at
kickoff.

## Environment

- [ ] `python3.11 --version` returns 3.11.x
- [ ] `git --version` returns 2.x
- [ ] `gh auth status` shows logged in to GitHub as `Robby955`
- [ ] `cd ~/Projects/theorempath/prophet-hacks`
- [ ] `git status` clean, on the branch you expect (or `main` if PR has merged)

## Virtualenv

- [ ] `python3.11 -m venv .venv`
- [ ] `source .venv/bin/activate`
- [ ] `python --version` inside the venv returns 3.11.x
- [ ] `pip install -r requirements.txt` completes without error
- [ ] `python -c "import ai_prophet_core; print(ai_prophet_core.__version__)"` prints `0.1.4`

## Secrets

- [ ] `cp .env.example .env`
- [ ] Open `.env` and fill in:
  - [ ] `PA_SERVER_API_KEY` (from Prophet Hacks kickoff materials)
  - [ ] `OPENAI_API_KEY`
  - [ ] `ANTHROPIC_API_KEY`
- [ ] `grep -c '=' .env` shows the expected count (at least 3)
- [ ] `git status` confirms `.env` is NOT staged (gitignored)

## Smoke

- [ ] `python -m pytest tests/ -v` runs from the repo root and prints all-green; do not proceed if any test fails.
- [ ] `bash scripts/agent/verify.sh` runs the full gate (config + tests + dry-run) and exits 0.
- [ ] `python agent.py --slug smoke --dry-run` exits 0 and prints the config hash
- [ ] `python agent.py --slug smoke --once` runs exactly one tick and exits cleanly (BenchmarkSession lifecycle path)
- [ ] SIGINT (Ctrl-C) on a running `python agent.py --slug smoke` triggers `received signal ... shutting down after current tick` and exits 0
- [ ] Live monitor: `bash scripts/run_monitor.sh` renders `traces/live.html` and refreshes every 5s
- [ ] Once Prophet Arena exposes the live endpoint: `python agent.py --slug smoke-real --variant baseline-market-price` runs one tick
- [ ] `cat trace/smoke-real/*.jsonl | head -1 | python -m json.tool` confirms the 21-field schema lands cleanly
- [ ] Re-running `python agent.py --slug smoke-real --variant baseline-market-price` resumes from the next tick (same experiment_id printed)

## Repo

- [ ] `gh repo create Robby955/prophet-hacks --private --source=. --remote=origin --push`
- [ ] `gh repo view Robby955/prophet-hacks --web` opens the new repo
- [ ] (Optional) Codex login confirmed: `codex --version`; `gh auth status` as the Codex user can push

## What to do FIRST when kickoff starts

1. Read the kickoff briefing end to end before writing anything new. Specifically: what is the actual SDK endpoint URL, what is the experiment-creation flow, and are there per-tick or per-team model-cost caps that are not yet in `risk.py`.
2. Ask the organizers in Discord / Slack / Q&A:
   - Is there historical resolution data we can backtest a forecaster against during the event?
   - Is there a sandbox or test mode for development, or is every call against scored live state?
   - Does submission scoring run on platform infra or ours? Who pays the LLM bill?
3. Run `python agent.py --slug smoke-real --variant baseline-market-price` ASAP. Confirm the JSONL trace lands. Only after that is green do we plug an LLM into a non-baseline variant.

## If anything fails

See `docs/RUNBOOK.md` for incident patterns.
