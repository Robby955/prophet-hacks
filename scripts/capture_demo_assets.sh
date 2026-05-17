#!/usr/bin/env bash
# Capture no-secret demo assets for ForecastingPath.
#
# By default this starts a local server with dashboard auth and provider keys
# unset, an empty prediction store, and the production variant name pinned.
# It records a short browser walkthrough plus screenshots under
# output/playwright/forecast-demo/. No /predict or /demo/start call is made.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT/output/playwright/forecast-demo"
PORT="${PORT:-8765}"
BASE_URL="${BASE_URL:-}"
SESSION="${SESSION:-forecast-demo}"
STARTED_SERVER=0
SERVER_PID=""
TMP_STORE_DIR=""

usage() {
  cat <<'EOF'
Usage: scripts/capture_demo_assets.sh [--base-url URL] [--out-dir DIR]

Options:
  --base-url URL   Capture against an already-running server instead of
                   starting a local clean server.
  --out-dir DIR    Output directory. Default: output/playwright/forecast-demo.

Environment:
  PORT             Local port when starting a clean server. Default: 8765.
  SESSION          Playwright session name. Default: forecast-demo.
  PWCLI            Optional path to playwright_cli.sh.

Outputs:
  01-public-root-desktop.png
  01b-public-root-mobile.png
  02-dashboard-first-call.png
  03-observatory-empty.png
  04-pipeline-trace.png
  05-abstain-slider.png
  06-heatmap-resolved.png
  07-scatter-resolved.png
  08-review-brief.png
  forecastingpath-walkthrough.webm

No /predict or /demo/start call is made.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="${2:-}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v npx >/dev/null 2>&1; then
  echo "npx is required for the Playwright wrapper" >&2
  exit 2
fi

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
PWCLI="${PWCLI:-$CODEX_HOME/skills/playwright/scripts/playwright_cli.sh}"
if [[ ! -x "$PWCLI" ]]; then
  echo "Playwright wrapper not found or not executable: $PWCLI" >&2
  exit 2
fi

cleanup() {
  set +e
  "$PWCLI" "-s=$SESSION" close >/dev/null 2>&1
  if [[ "$STARTED_SERVER" == "1" && -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1
    wait "$SERVER_PID" >/dev/null 2>&1
  fi
  if [[ -n "$TMP_STORE_DIR" ]]; then
    rm -rf "$TMP_STORE_DIR"
  fi
}
trap cleanup EXIT

wait_for_health() {
  local url="$1"
  for _ in {1..40}; do
    if curl -fsS "$url/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "server did not become healthy: $url" >&2
  return 1
}

mkdir -p "$OUT_DIR"

if [[ -z "$BASE_URL" ]]; then
  TMP_STORE_DIR="$(mktemp -d)"
  touch "$TMP_STORE_DIR/predictions.jsonl"
  COMMIT="$(git -C "$ROOT" rev-parse --short=8 HEAD 2>/dev/null || echo dev)"
  BASE_URL="http://127.0.0.1:$PORT"
  (
    cd "$ROOT"
    env \
      -u DASHBOARD_AUTH_TOKEN \
      -u DASHBOARD_PIN \
      -u PA_SERVER_API_KEY \
      -u ANTHROPIC_API_KEY \
      -u OPENAI_API_KEY \
      -u BRAVE_SEARCH_API_KEY \
      PROPHET_AGENT_VARIANT="${PROPHET_AGENT_VARIANT:-multi_outcome_retrieval}" \
      PROPHET_BUILD_COMMIT_SHA="$COMMIT" \
      PROPHET_PREDICTION_STORE_PATH="$TMP_STORE_DIR/predictions.jsonl" \
      PATH="$ROOT/.venv/bin:$PATH" \
      python -m uvicorn forecast_agent_server:app --host 127.0.0.1 --port "$PORT" \
        > "$OUT_DIR/server.log" 2>&1
  ) &
  SERVER_PID="$!"
  STARTED_SERVER=1
  wait_for_health "$BASE_URL"
fi

"$PWCLI" "-s=$SESSION" open "$BASE_URL/" --headed
"$PWCLI" "-s=$SESSION" resize 1440 900
"$PWCLI" "-s=$SESSION" screenshot --filename "$OUT_DIR/01-public-root-desktop.png"
"$PWCLI" "-s=$SESSION" resize 390 844
"$PWCLI" "-s=$SESSION" screenshot --filename "$OUT_DIR/01b-public-root-mobile.png"
"$PWCLI" "-s=$SESSION" resize 1440 900
"$PWCLI" "-s=$SESSION" video-start "$OUT_DIR/forecastingpath-walkthrough.webm" --size 1440x900

"$PWCLI" "-s=$SESSION" goto "$BASE_URL/dashboard"
"$PWCLI" "-s=$SESSION" screenshot --filename "$OUT_DIR/02-dashboard-first-call.png"
"$PWCLI" "-s=$SESSION" goto "$BASE_URL/observatory"
"$PWCLI" "-s=$SESSION" screenshot --filename "$OUT_DIR/03-observatory-empty.png"
"$PWCLI" "-s=$SESSION" goto "$BASE_URL/static/pipeline_trace.html"
"$PWCLI" "-s=$SESSION" screenshot --filename "$OUT_DIR/04-pipeline-trace.png"
"$PWCLI" "-s=$SESSION" goto "$BASE_URL/static/abstain_slider.html"
"$PWCLI" "-s=$SESSION" eval "() => { const slider = document.getElementById('threshold'); if (slider) { slider.value = '0.20'; slider.dispatchEvent(new Event('input', { bubbles: true })); } }" >/dev/null
"$PWCLI" "-s=$SESSION" screenshot --filename "$OUT_DIR/05-abstain-slider.png"
"$PWCLI" "-s=$SESSION" goto "$BASE_URL/static/heatmap_resolved.html"
"$PWCLI" "-s=$SESSION" screenshot --filename "$OUT_DIR/06-heatmap-resolved.png"
"$PWCLI" "-s=$SESSION" goto "$BASE_URL/static/scatter_resolved.html"
"$PWCLI" "-s=$SESSION" screenshot --filename "$OUT_DIR/07-scatter-resolved.png"
"$PWCLI" "-s=$SESSION" goto "$BASE_URL/review"
"$PWCLI" "-s=$SESSION" screenshot --filename "$OUT_DIR/08-review-brief.png"
"$PWCLI" "-s=$SESSION" video-stop

ls -lh "$OUT_DIR"
