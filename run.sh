#!/usr/bin/env bash
# Prophet Hacks 2026 — single-entrypoint run script for organizers.
#
# Starts the local FastAPI forecasting server on http://127.0.0.1:8000
# with the production variant (multi_outcome_retrieval). Once it's up,
# Prophet Arena's evaluation harness (or `curl`) can POST /predict.
#
# Requires Python 3.13+ and the env vars in .env (see .env.example).
#
# Usage:
#   ./run.sh                  # start server
#   ./run.sh smoke            # one-shot smoke test against the live deploy
#   ./run.sh backtest         # rerun the headline 26-event backtest
#   ./run.sh test             # run the test suite

set -euo pipefail

cd "$(dirname "$0")"

MODE="${1:-server}"

if [[ ! -d .venv ]]; then
  echo "==> creating venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Install deps idempotently. Skip if already satisfied.
if ! python -c "import fastapi, anthropic, httpx" >/dev/null 2>&1; then
  echo "==> installing dependencies"
  pip install --quiet -r requirements.txt
fi

# Load .env if present (production runs from process env vars on Railway).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

case "$MODE" in
  server)
    echo "==> starting forecasting server on http://127.0.0.1:8000"
    echo "    POST /predict to forecast; GET /healthz to verify"
    : "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY required — see .env.example}"
    : "${BRAVE_SEARCH_API_KEY:=}"  # optional; agent degrades gracefully without it
    : "${PROPHET_AGENT_VARIANT:=multi_outcome_retrieval}"
    exec uvicorn forecast_agent_server:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  smoke)
    HOST="${HOST:-https://agent.forecastingpath.com}"
    echo "==> smoke-testing $HOST"
    curl -fsS "$HOST/healthz" | python -m json.tool
    echo
    curl -fsS -X POST "$HOST/predict" \
      -H "content-type: application/json" \
      -d '{"event_ticker":"smoke-1","market_ticker":"smoke-1","title":"Will Bitcoin close above $100k on 2026-12-31?","outcomes":["Yes","No"],"close_time":"2026-12-31T23:59:59Z","description":"Smoke test","rules":"YES if BTC closes above 100000 USD on 2026-12-31."}' \
      | python -m json.tool
    ;;
  backtest)
    : "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY required for backtest}"
    : "${BRAVE_SEARCH_API_KEY:?BRAVE_SEARCH_API_KEY required for retrieval}"
    echo "==> rerunning headline 26-event backtest (~3 min, ~$2 in API)"
    python scripts/backtest_forecast.py --variants multi_outcome_retrieval
    ;;
  test)
    echo "==> running test suite"
    python -m pytest tests/ -q
    ;;
  *)
    echo "usage: ./run.sh [server|smoke|backtest|test]" >&2
    exit 2
    ;;
esac
