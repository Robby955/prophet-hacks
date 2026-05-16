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

- **Current task:** Private live dashboard, public root status page, and apex DNS verification.
- **Files owned this session:** `forecast_agent_server.py`, `tests/test_forecast_agent_server.py`, `.env.example`, `README.md`, `docs/STATUS.yaml`, `docs/STATUS.html`, `docs/LIVE_OPERATIONS.md`, `docs/DECISIONS.md`, `docs/AGENT_STATUS.md`
- **Last updated:** 2026-05-16T18:46:08Z
- **Notes:** Railway now lists `forecastingpath.com` on `oracles-agent`. Cloudflare authoritative DNS shows apex propagation in progress. `/dashboard`, `/predictions`, and `/events` are moving behind `DASHBOARD_AUTH_TOKEN`; `/predict`, `/healthz`, and `/` stay public.

## (template) <agent or worktree name>

- **Current task:** `<one line>`
- **Files owned this session:** `<paths>`
- **Last updated:** `<ISO timestamp>`
- **Notes:** `<optional>`
