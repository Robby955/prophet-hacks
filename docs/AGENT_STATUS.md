# Agent status board

Live state of every coding agent active on this repo. Each agent updates
its own section on the first turn and again on the last turn of a work
session.

Rule: if your section says you own `agent.py` or `config.yaml`, no other
agent may modify those files in the same session. Touch supporting modules
instead (`market_filter.py`, `forecaster.py`, `risk.py`, `retrieval/`, `prompts/`).

Format: one `## <agent name / worktree>` heading per agent, body has:
- **Current task:** one line
- **Files owned this session:** list of paths
- **Last updated:** ISO timestamp (UTC)
- **Notes:** optional, any blockers or hand-off context

---

## main (Rob, direct edits)

- **Current task:** idle.
- **Files owned this session:** none.
- **Last updated:** 2026-05-15T00:00:00Z (seed entry).
- **Notes:** Rob holds final-say on `agent.py`, `config.yaml`, and anything in `docs/`. Other agents propose via branches; Rob merges.

## codex/main-orientation

- **Current task:** Code-review follow-up for evaluation helpers and agent handoff docs.
- **Files owned this session:** `agent_protocol.md`, `evaluation/`, `forecasting/`, `scripts/watch_predictions.sh`, `tests/test_evaluation.py`, `tests/test_forecast_agent_server.py`, `docs/DECISIONS.md`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-16T19:19:43Z
- **Notes:** Review found no critical production-path regression. Important fixes are validation for evaluation metrics, return math for NO contracts, current-state handoff docs, and SSE auth coverage.
- **Heads-up (added 2026-05-16T19:58Z):** Your snapshot reading `main = a254665, deploy 1130735a SUCCESS` is now stale. Phase 2 has landed: commit `9652016` swaps `predict_multi_outcome_retrieval` to Opus 4.7, adds a market-odds anchoring block to its system prompt, and fixes a binary-clamp bug in `longshot_guard_floor` (was 0.25 for n=2, now capped at 0.10 = Kalshi threshold). Deploy `415edc6e` SUCCESS at 19:54Z; live smoke confirms (Knicks/SB 2027 returns 0.10, was 0.25). **Before any "current state" claim, run `git fetch && git log --oneline -5` AND `curl -s https://agent.forecastingpath.com/healthz | jq .commit` so we agree on reality.** From now on, deploys go via `./scripts/agent/deploy.sh` (preflight checks upload size; this would have prevented the 18MB `.claude/` worktree bloat that broke three of my deploys earlier).

## claude/phase-2-and-cicd

- **Current task:** Phase 2 (Opus 4.7 + market anchoring + floor bug fix) shipped + CI/CD hardening (preflight, deploy wrapper, /healthz commit SHA).
- **Files owned this session:** `forecast_track.py`, `forecast_agent_server.py`, `config.yaml`, `scripts/preflight.sh`, `scripts/agent/deploy.sh`, `scripts/analyze_results.py`, `docs/HANDOFF.md`, `docs/DECISIONS.md`, `.gitignore`.
- **Last updated:** 2026-05-16T19:58:00Z
- **Notes:** Deploy is automated and verified-live. Watcher PID 33661 active. Open PRs #1 (calibrator + ensemble — Phase 3 candidate) and #4 (SAE stack — Phase 3 after first PA call). Waiting for first Prophet Arena call.

## (template) <agent or worktree name>

- **Current task:** `<one line>`
- **Files owned this session:** `<paths>`
- **Last updated:** `<ISO timestamp>`
- **Notes:** `<optional>`
