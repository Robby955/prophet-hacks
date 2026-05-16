#!/usr/bin/env bash
# Launch the live HTML monitor and a tiny static file server.
#
# Renders trace/live.html every 5s (or PROPHET_MONITOR_REFRESH). A second
# process serves the trace directory on PROPHET_MONITOR_PORT (default 8765)
# so a phone or another laptop on the LAN can load the page.
#
# Stop with Ctrl-C; both background processes are cleaned up via trap.
set -euo pipefail

REFRESH="${PROPHET_MONITOR_REFRESH:-5}"
PORT="${PROPHET_MONITOR_PORT:-8765}"
TRACE_DIR="${PROPHET_TRACE_PATH:-trace}"
OUTPUT_DIR="${TRACE_DIR}"
OUTPUT_FILE="${OUTPUT_DIR}/live.html"

mkdir -p "${OUTPUT_DIR}"

cd "$(dirname "$0")/.."

python monitor/live_monitor.py \
  --trace-dir "${TRACE_DIR}" \
  --output "${OUTPUT_FILE}" \
  --refresh "${REFRESH}" &
MONITOR_PID=$!

(cd "${OUTPUT_DIR}" && python -m http.server "${PORT}") &
SERVER_PID=$!

cleanup() {
  kill "${MONITOR_PID}" "${SERVER_PID}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "live monitor: http://localhost:${PORT}/live.html (refresh ${REFRESH}s)"
wait "${MONITOR_PID}"
