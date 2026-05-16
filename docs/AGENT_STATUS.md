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

## (template) <agent or worktree name>

- **Current task:** `<one line>`
- **Files owned this session:** `<paths>`
- **Last updated:** `<ISO timestamp>`
- **Notes:** `<optional>`
