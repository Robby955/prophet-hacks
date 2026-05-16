#!/usr/bin/env bash
# Poll Prophet Arena every 60s for state changes and ping Rob via macOS
# notification when something interesting happens. Watches three signals:
#
#   1. Endpoint last_run_at moved from null to a real timestamp
#      => Prophet Arena called our /predict for the first time.
#   2. Number of open events went 0 -> >0
#      => hackathon-day events are being posted.
#   3. /forecast/scores went from "no scores" to having scores
#      => first scoring round happened.
#
# Usage (foreground, prints each poll):
#   bash scripts/watch_predictions.sh
#
# Usage (background, silent except for notifications):
#   nohup bash scripts/watch_predictions.sh > /tmp/oracles_watch.log 2>&1 &

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd -P)"
PY="$REPO_ROOT/.venv/bin/python"

POLL_SEC="${POLL_SEC:-60}"
STATE_FILE="${STATE_FILE:-/tmp/oracles_watch_state}"

notify() {
  local title="$1"; shift
  local body="$*"
  echo "[$(date '+%H:%M:%S')] NOTIFY: $title — $body"
  if command -v osascript > /dev/null; then
    osascript -e "display notification \"$body\" with title \"$title\" sound name \"Glass\"" 2>/dev/null || true
  fi
}

read_state() {
  if [[ -f "$STATE_FILE" ]]; then cat "$STATE_FILE"; else echo "{}"; fi
}

write_state() {
  echo "$1" > "$STATE_FILE"
}

# Initial state if file doesn't exist
if [[ ! -f "$STATE_FILE" ]]; then
  write_state '{"last_run_at": null, "n_open": 0, "scored": false}'
fi

echo "watching every ${POLL_SEC}s. state file: $STATE_FILE"

while true; do
  prev_state="$(read_state)"
  json="$("$PY" - <<'PYEOF'
import json, os, httpx, sys
from dotenv import load_dotenv
load_dotenv(os.path.join(os.environ['PWD'], '.env'))
api_key = os.environ.get('PA_SERVER_API_KEY', '')
team = os.environ.get('PA_TEAM_NAME', 'CanadaHacks')
headers = {'X-API-Key': api_key}
state = {'last_run_at': None, 'n_open': 0, 'scored': False}
try:
    r = httpx.get(f'https://api.aiprophet.dev/forecast/endpoints/{team}', headers=headers, timeout=10)
    if r.status_code == 200:
        state['last_run_at'] = (r.json() or {}).get('last_run_at')
except Exception:
    pass
try:
    r = httpx.get('https://api.aiprophet.dev/forecast/events?status=open', headers=headers, timeout=10)
    if r.status_code == 200:
        d = r.json()
        if isinstance(d, list): state['n_open'] = len(d)
except Exception:
    pass
try:
    r = httpx.get('https://api.aiprophet.dev/forecast/scores', headers=headers, timeout=10)
    if r.status_code == 200:
        d = r.json()
        state['scored'] = bool(d) if not isinstance(d, dict) or 'detail' not in d else False
except Exception:
    pass
print(json.dumps(state))
PYEOF
)"

  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "[$ts] $json"

  # Diff against prev_state
  prev_last="$(echo "$prev_state" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("last_run_at"))')"
  curr_last="$(echo "$json" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("last_run_at"))')"
  prev_n="$(echo "$prev_state" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("n_open", 0))')"
  curr_n="$(echo "$json" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("n_open", 0))')"
  prev_scored="$(echo "$prev_state" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("scored", False))')"
  curr_scored="$(echo "$json" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("scored", False))')"

  if [[ "$prev_last" == "None" && "$curr_last" != "None" ]]; then
    notify "Oracles: FIRST CALL" "Prophet Arena just called our /predict (last_run_at=$curr_last). Check the dashboard."
  fi
  if [[ "$prev_n" == "0" && "$curr_n" -gt "0" ]]; then
    notify "Oracles: open events" "$curr_n open events on Prophet Arena. Check 'prophet forecast events --status open'."
  fi
  if [[ "$prev_scored" == "False" && "$curr_scored" == "True" ]]; then
    notify "Oracles: first scores" "First scores posted to /forecast/scores."
  fi

  write_state "$json"
  sleep "$POLL_SEC"
done
