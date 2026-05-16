# Worktree protocol

How multiple coding agents (Claude Code dispatch, Codex, Rob himself)
work on this repo concurrently without clobbering each other.

## One agent, one worktree

- All non-Rob coding work happens in a git worktree under `.claude/worktrees/<agent-descriptor>/`.
- Each worktree branches off `main` and never touches `main` directly.
- Start a worktree: `git worktree add .claude/worktrees/<descriptor> -b feat/<dated-slug>`.
- Remove a worktree when done: `git worktree remove .claude/worktrees/<descriptor>` (after the branch is merged).

## File ownership during a session

- The critical-path files are `agent.py` and `config.yaml`. Only one agent at a time may modify them.
- Other agents working at the same time touch supporting modules:
  - `market_filter.py`
  - `forecaster.py`
  - `risk.py`
  - `retrieval/` (when wired)
  - `prompts/` (when wired)
- Before editing any critical-path file, update `docs/AGENT_STATUS.md` to claim it. Other agents read that file first and pick a different file if it is already claimed.

## Identity and commit hygiene

- Every worktree inherits the local git config, but verify before the first commit on each worktree:
  - `git config --local --get user.email` must equal `robbysneiderman@gmail.com` (no dot).
  - `git config --local --get user.name` should be `Rob Sneiderman`.
- Never include AI co-author trailers in commit messages.
- Commit-message convention: `<scope>(<area>): <one-line>`, e.g. `feat(forecaster): wire opus-4-7 single-call variant`.
- Always create new commits; never `git commit --amend` a pushed commit.

## Merging back to main

- Before pushing: `git fetch origin && git rebase origin/main`. Resolve conflicts in the worktree.
- Run the smoke import: `python -c "import sys; sys.path.insert(0, '.'); import agent, risk, forecaster, market_filter, logger"`.
- Run the dry-run: `python agent.py --slug smoke --dry-run`.
- Squash-merge the feature branch into `main`. Keep `main` linear.
- Delete the feature branch locally and remotely after merge.

## Conflict resolution

- Two agents disagree on direction (e.g. different forecaster architectures): escalate to Rob via an `AGENT_STATUS.md` update with a `Notes:` line describing both options. Do not silently resolve in-agent.
- Two branches edited the same file: prefer the smaller, more recent change as a base and re-apply the larger change on top, by hand.

## Already covered by `.gitignore`

- `.venv/`
- `logs/*.jsonl`
- `trace/*` (with `!trace/.gitkeep`)
- `.env` and `.env.*` (with `!.env.example`)
- `__pycache__/`, `*.pyc`

If you find yourself wanting to commit something under any of those paths, stop and ask Rob first.
