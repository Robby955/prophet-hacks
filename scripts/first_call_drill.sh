#!/usr/bin/env bash
# Zero-spend rehearsal for the first Prophet Arena call.
#
# This intentionally does not call /predict. It checks the surfaces we need
# in the first ten minutes after PA finally hits the endpoint.

set -uo pipefail

HOST="https://agent.forecastingpath.com"
TOKEN="${DASHBOARD_AUTH_TOKEN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="${2:-}"
      shift 2
      ;;
    --token)
      TOKEN="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/first_call_drill.sh [--host URL] [--token DASHBOARD_TOKEN]

Checks:
  - /healthz shape and commit
  - public root has the run preview and no stale disclosure copy
  - static research HTML is auth-gated
  - /predictions shape when a token is available
  - demo routes require auth without starting a demo run
  - local watcher process is alive

No /predict call is made.
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

HOST="${HOST%/}"
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

green() { printf "\033[32m%s\033[0m" "$1"; }
red()   { printf "\033[31m%s\033[0m" "$1"; }
yellow(){ printf "\033[33m%s\033[0m" "$1"; }
ok()    { echo "  $(green OK)   $1"; PASS_COUNT=$((PASS_COUNT+1)); }
fail()  { echo "  $(red FAIL) $1"; FAIL_COUNT=$((FAIL_COUNT+1)); }
warn()  { echo "  $(yellow WARN) $1"; WARN_COUNT=$((WARN_COUNT+1)); }

json_get() {
  local key="$1"
  python3 -c "import json,sys; print(json.load(sys.stdin).get('$key',''))" 2>/dev/null
}

echo "=== first-call drill against $HOST ==="

echo "[1/6] /healthz"
HEALTH=$(curl -sS --max-time 10 "$HOST/healthz" 2>/tmp/first_call_health.err)
if [[ -z "$HEALTH" ]]; then
  fail "/healthz empty response: $(cat /tmp/first_call_health.err 2>/dev/null)"
else
  STATUS=$(echo "$HEALTH" | json_get status)
  COMMIT=$(echo "$HEALTH" | json_get commit)
  VARIANT=$(echo "$HEALTH" | json_get variant)
  if [[ "$STATUS" == "ok" && -n "$COMMIT" && -n "$VARIANT" ]]; then
    ok "/healthz status=ok commit=$COMMIT variant=$VARIANT"
  else
    fail "/healthz unexpected payload: $(echo "$HEALTH" | head -c 220)"
  fi
fi

echo "[2/6] public root copy and preview"
ROOT=$(curl -sS -L --max-time 10 "$HOST/" 2>/tmp/first_call_root.err)
if [[ -z "$ROOT" ]]; then
  fail "root empty response: $(cat /tmp/first_call_root.err 2>/dev/null)"
else
  if echo "$ROOT" | grep -q 'class="run-window"' && echo "$ROOT" | grep -q "Open console"; then
    ok "public root has run preview and console link"
  else
    fail "public root missing run preview or console link"
  fi
  if echo "$ROOT" | grep -Eq "PIN only|stay behind|Detailed traces|Restricted observatory|Brave Search|GPT-5.5|Gemini"; then
    fail "public root exposes stale disclosure or internal model copy"
  else
    ok "public root has no stale disclosure/internal model copy"
  fi
fi

echo "[3/6] static research auth gates"
for path in \
  /static/summary.html \
  /static/gallery_resolved.html \
  /static/abstain_slider.html \
  /static/scatter_resolved.html \
  /static/heatmap_resolved.html \
  /static/bootstrap_hist.html \
  /static/pipeline_trace.html
do
  CODE=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 "$HOST$path")
  if [[ "$CODE" == "401" || "$CODE" == "303" || "$CODE" == "302" ]]; then
    ok "$path unauthenticated code=$CODE"
  else
    fail "$path should be auth-gated, got code=$CODE"
  fi
done

echo "[4/6] /predictions inspection path"
if [[ -z "$TOKEN" ]]; then
  warn "no dashboard token supplied; skipping authenticated /predictions shape"
else
  PRED=$(curl -sS --max-time 10 "$HOST/predictions" -H "x-dashboard-token: $TOKEN")
  if COUNT=$(echo "$PRED" | python3 -c "import json,sys; d=json.load(sys.stdin); predictions=d.get('predictions'); assert isinstance(predictions, list); print(len(predictions))" 2>/dev/null); then
    ok "/predictions authenticated shape ok; count=$COUNT"
  else
    fail "/predictions authenticated response unexpected: $(echo "$PRED" | head -c 220)"
  fi
fi

echo "[5/6] demo routes require auth without starting a run"
DEMO_CODE=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 "$HOST/demo/result/missing")
if [[ "$DEMO_CODE" == "401" ]]; then
  ok "/demo/result/missing requires auth"
else
  fail "/demo/result/missing expected 401 unauthenticated, got $DEMO_CODE"
fi

echo "[6/6] local watcher process"
if pgrep -f watch_predictions >/dev/null; then
  PID=$(pgrep -f watch_predictions | head -1)
  AGE=$(ps -o etime= -p "$PID" 2>/dev/null | xargs)
  ok "watcher PID $PID alive ($AGE)"
else
  warn "watch_predictions process not found; start with: nohup bash scripts/watch_predictions.sh > /tmp/oracles_watch.log 2>&1 &"
fi

echo ""
echo "=== summary ==="
echo "  $(green "$PASS_COUNT pass") · $(yellow "$WARN_COUNT warn") · $(red "$FAIL_COUNT fail")"
exit "$FAIL_COUNT"
