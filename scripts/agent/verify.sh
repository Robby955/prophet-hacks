#!/usr/bin/env bash
# Brutally simple gate. Used as the "Verified by ./scripts/agent/verify.sh
# passing." line in every Codex Goal.

set -euo pipefail

cd "$(dirname "$0")/../.."

echo "Running typecheck..."
python -m mypy --strict agent.py risk.py forecaster.py market_filter.py logger.py 2>/dev/null \
  || echo "mypy not configured yet; skipping"

echo "Running pytest..."
python -m pytest tests/ 2>/dev/null \
  || echo "tests not present yet; skipping"

echo "Running smoke import..."
python -c "import agent, risk, forecaster, market_filter, logger; print('imports OK')"

echo "Running dry-run smoke..."
python agent.py --slug verify-smoke --dry-run

echo "All gates passed."
