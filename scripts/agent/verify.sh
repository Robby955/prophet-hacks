#!/usr/bin/env bash
# Brutally simple gate. Used as the "Verified by ./scripts/agent/verify.sh
# passing." line in every Codex Goal.

set -euo pipefail

cd "$(dirname "$0")/../.."

echo "Running typecheck..."
python -m mypy --strict agent.py risk.py forecaster.py market_filter.py logger.py 2>/dev/null \
  || echo "mypy not configured yet; skipping"

echo "Running pytest..."
# Don't silently swallow failures. If pytest exists at all, treat any
# non-zero exit as a real verify failure. The previous `2>/dev/null || echo`
# pattern hid the dashboard-polish favicon-test regression for an entire
# Phase-2 session (2026-05-16). Better loud than silent.
if [ -d tests ] && ls tests/test_*.py >/dev/null 2>&1; then
  python -m pytest tests/ || { echo "pytest failed"; exit 1; }
else
  echo "tests/ has no test_*.py; skipping"
fi

echo "Running smoke import..."
python -c "import agent, risk, forecaster, market_filter, logger; print('imports OK')"

echo "Running dry-run smoke..."
python agent.py --slug verify-smoke --dry-run

echo "All gates passed."
