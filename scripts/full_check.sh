#!/usr/bin/env bash
# End-to-end "is everything OK?" check. Run this before sleeping, before
# the event window opens, or any time the system needs a sanity audit.
#
# What it exercises:
#   1. Verify gate (tests + smoke import + dry-run)
#   2. Working tree clean + HEAD pushed (via preflight.sh logic)
#   3. Local HEAD vs deployed SHA
#   4. /healthz returns expected shape (variant, commit, status)
#   5. Brave Search reliability monitor (retrieval dependency healthy)
#   6. /predict end-to-end with a synthetic event (live API call, ~$0.10)
#   7. /login serves the PIN form (auth surface up)
#   8. /dashboard redirects browser visitors to /login (auth gate works)
#   9. /predictions returns JSON 401 to API callers (auth gate works)
#  10. Static artifact auth/public behavior is correct
#  11. Watcher process alive (mac notifications working)
#
# Pass criteria: every check prints "OK". Non-zero exit otherwise.
#
# Usage:
#   ./scripts/full_check.sh                 # against production
#   ./scripts/full_check.sh --host http://localhost:8000  # against local

set -uo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd -P)"
HOST="https://agent.forecastingpath.com"
SKIP_SMOKE_CALL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2;;
    --skip-smoke) SKIP_SMOKE_CALL=1; shift;;
    *) shift;;
  esac
done

PASS_COUNT=0
FAIL_COUNT=0

green() { printf "\033[32m%s\033[0m" "$1"; }
red()   { printf "\033[31m%s\033[0m" "$1"; }
ok()    { echo "  $(green OK)   $1"; PASS_COUNT=$((PASS_COUNT+1)); }
fail()  { echo "  $(red FAIL) $1"; FAIL_COUNT=$((FAIL_COUNT+1)); }

echo "=== full check against $HOST ==="

# 1. Verify gate (silently, surface result)
echo "[1/11] verify gate"
if PATH="$REPO_ROOT/.venv/bin:$PATH" "$REPO_ROOT/scripts/agent/verify.sh" \
    > /tmp/full_check_verify.log 2>&1; then
  ok "verify gate green"
else
  fail "verify gate failed; see /tmp/full_check_verify.log"
fi

# 2. Working tree + push state
echo "[2/11] git state"
if ! git diff --quiet HEAD 2>/dev/null; then
  fail "uncommitted changes; commit or stash"
elif [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  fail "untracked non-ignored files; commit or .gitignore them"
else
  git fetch origin --quiet 2>/dev/null
  LOCAL=$(git rev-parse HEAD)
  REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "")
  if [[ -n "$REMOTE" && "$LOCAL" != "$REMOTE" ]]; then
    fail "HEAD ${LOCAL:0:8} != origin/main ${REMOTE:0:8}; push first"
  else
    ok "working tree clean, HEAD = origin/main = ${LOCAL:0:8}"
  fi
fi

# 3. Local HEAD vs deployed SHA
echo "[3/11] local vs deployed SHA"
LOCAL_SHA=$(git rev-parse --short=8 HEAD)
LIVE_SHA=$(curl -s --max-time 5 "$HOST/healthz" 2>/dev/null \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('commit','?'))" 2>/dev/null || echo "?")
if [[ "$LOCAL_SHA" == "$LIVE_SHA" ]]; then
  ok "local HEAD = live commit = $LOCAL_SHA"
elif [[ "$LIVE_SHA" == "?" ]]; then
  fail "live /healthz unreachable"
else
  echo "  $(red WARN) local=$LOCAL_SHA live=$LIVE_SHA — deploy needed"
fi

# 4. /healthz shape
echo "[4/11] /healthz response shape"
HEALTHZ=$(curl -s --max-time 5 "$HOST/healthz")
if [[ -z "$HEALTHZ" ]]; then
  fail "/healthz empty response"
else
  for key in status team project variant version commit; do
    if echo "$HEALTHZ" | python3 -c "import json,sys; sys.exit(0 if '$key' in json.load(sys.stdin) else 1)" 2>/dev/null; then
      : # silent per-key
    else
      fail "/healthz missing required key '$key'"
    fi
  done
  STATUS=$(echo "$HEALTHZ" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status'))" 2>/dev/null)
  VARIANT=$(echo "$HEALTHZ" | python3 -c "import json,sys; print(json.load(sys.stdin).get('variant'))" 2>/dev/null)
  if [[ "$STATUS" == "ok" && "$VARIANT" == "multi_outcome_retrieval" ]]; then
    ok "/healthz status=ok variant=$VARIANT"
  else
    fail "/healthz status=$STATUS variant=$VARIANT"
  fi
fi

# 5. Brave Search reliability monitor
echo "[5/11] Brave Search health"
BRAVE_OUT=$("$REPO_ROOT/scripts/brave_health.sh" --quiet 2>&1)
BRAVE_CODE=$?
if [[ "$BRAVE_CODE" == "0" ]]; then
  ok "Brave Search healthy"
else
  BRAVE_SUMMARY=$(echo "$BRAVE_OUT" | tr '\n' ' ' | head -c 220)
  fail "Brave Search unhealthy/degraded (exit $BRAVE_CODE): $BRAVE_SUMMARY"
fi

# 6. /predict end-to-end smoke (live API call, ~$0.10)
echo "[6/11] /predict end-to-end smoke"
if [[ -z "$SKIP_SMOKE_CALL" ]]; then
  SMOKE='{"event_ticker":"FULL-CHECK","market_ticker":"FULL-CHECK","title":"Will the test pass?","category":"Test","close_time":"2027-01-01T00:00:00Z","outcomes":["Yes","No"]}'
  RESP=$(curl -s -X POST "$HOST/predict" \
    -H "Content-Type: application/json" \
    -d "$SMOKE" --max-time 60)
  if echo "$RESP" | python3 -c "
import json, sys
d = json.load(sys.stdin)
probs = d.get('probabilities', [])
assert isinstance(probs, list) and len(probs) == 2
assert all('market' in p and 'probability' in p for p in probs)
for p in probs:
    assert 0.0 <= p['probability'] <= 1.0
print('OK')
" 2>/dev/null | grep -q OK; then
    ok "/predict returned valid 2-outcome probabilities"
  else
    fail "/predict response invalid: $(echo "$RESP" | head -c 200)"
  fi
else
  echo "  (skipped, --skip-smoke)"
fi

# 7. /login serves the PIN form
echo "[7/11] /login PIN form"
LOGIN_BODY=$(curl -s --max-time 5 "$HOST/login")
if echo "$LOGIN_BODY" | grep -q "Sign in" && echo "$LOGIN_BODY" | grep -q 'name="pin"'; then
  ok "/login serves PIN entry form"
else
  fail "/login response unexpected: $(echo "$LOGIN_BODY" | head -c 100)"
fi

# 8. /dashboard redirects browser visitors to /login
echo "[8/11] /dashboard browser redirect to /login"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Accept: text/html" "$HOST/dashboard")
if [[ "$CODE" == "303" || "$CODE" == "307" || "$CODE" == "302" ]]; then
  ok "/dashboard browser redirect ($CODE)"
elif [[ "$CODE" == "401" ]]; then
  echo "  $(red WARN) /dashboard returned 401 instead of redirecting; DASHBOARD_PIN not set?"
else
  fail "/dashboard unexpected code $CODE"
fi

# 9. /predictions returns 401 to API callers
echo "[9/11] /predictions returns JSON 401"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Accept: application/json" "$HOST/predictions")
if [[ "$CODE" == "401" ]]; then
  ok "/predictions returns 401 to unauthenticated API callers"
else
  fail "/predictions unexpected code $CODE"
fi

# 10. Static artifacts: research HTML is auth-gated; PDF remains public
echo "[10/11] static artifact auth/public behavior"
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HOST/static/summary.html")
if [[ "$CODE" == "401" || "$CODE" == "303" || "$CODE" == "302" ]]; then
  ok "/static/summary.html unauthenticated code=$CODE"
else
  fail "/static/summary.html should be auth-gated, got code $CODE"
fi
if [[ -n "${DASHBOARD_AUTH_TOKEN:-}" ]]; then
  CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    -H "x-dashboard-token: $DASHBOARD_AUTH_TOKEN" "$HOST/static/summary.html")
  if [[ "$CODE" == "200" ]]; then
    ok "/static/summary.html authenticated serves 200"
  else
    fail "/static/summary.html authenticated unexpected code $CODE"
  fi
else
  echo "  $(red WARN) DASHBOARD_AUTH_TOKEN not set; skipping authenticated summary.html check"
fi
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HOST/static/summary.pdf")
if [[ "$CODE" == "200" ]]; then
  ok "/static/summary.pdf serves 200"
else
  fail "/static/summary.pdf unexpected code $CODE"
fi

# 11. Watcher alive
echo "[11/11] watcher process alive"
if pgrep -f watch_predictions > /dev/null; then
  PID=$(pgrep -f watch_predictions | head -1)
  AGE=$(ps -o etime= -p "$PID" 2>/dev/null | xargs)
  ok "watcher PID $PID alive ($AGE)"
else
  echo "  $(red WARN) no watch_predictions process; restart with:"
  echo "       nohup bash scripts/watch_predictions.sh > /tmp/oracles_watch.log 2>&1 &"
fi

echo ""
echo "=== summary ==="
echo "  $(green "$PASS_COUNT pass") · $(red "$FAIL_COUNT fail")"
exit $FAIL_COUNT
