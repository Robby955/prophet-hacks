#!/usr/bin/env bash
# Brave Search reliability check. Probes the API with a benign query and
# reports rate-limit-remaining + result count. Designed for cron / pre-event
# monitoring -- silent retrieval degradation is the most plausible "we
# shipped retrieval-less" failure mode in production, and it isn't
# detectable from /healthz alone.
#
# Exit codes:
#   0  healthy (>= 10 requests remaining + got >= 3 results)
#   1  degraded (some quota remaining but low, OR fewer results than expected)
#   2  unhealthy (HTTP error from Brave OR zero quota remaining)
#
# Usage:
#   ./scripts/brave_health.sh
#   ./scripts/brave_health.sh --quiet    # only print on degradation/failure
#
# Output:
#   status: ok | degraded | unhealthy
#   ratelimit_remaining: <per-second>, <per-month>
#   ratelimit_reset_seconds: <s_to_per_second_reset>, <s_to_monthly_reset>
#   results_count: <int>

set -uo pipefail

cd "$(dirname "$0")/.."

# Load .env so BRAVE_SEARCH_API_KEY is set
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

QUIET=""
[[ "${1:-}" == "--quiet" ]] && QUIET=1

if [[ -z "${BRAVE_SEARCH_API_KEY:-}" ]]; then
  echo "status: unhealthy" >&2
  echo "reason: BRAVE_SEARCH_API_KEY not set" >&2
  exit 2
fi

# Hit Brave with a benign, low-info query that's stable over time.
TMP=$(mktemp)
HTTP_CODE=$(curl -s -o "$TMP" \
  -D /tmp/brave_headers \
  -w "%{http_code}" \
  --max-time 10 \
  -H "X-Subscription-Token: $BRAVE_SEARCH_API_KEY" \
  -H "Accept: application/json" \
  "https://api.search.brave.com/res/v1/web/search?q=Federal+Reserve+meeting&count=3")

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "status: unhealthy"
  echo "reason: HTTP $HTTP_CODE from Brave"
  echo "body_preview: $(head -c 200 "$TMP")"
  rm -f "$TMP" /tmp/brave_headers
  exit 2
fi

# Parse rate-limit headers. Brave returns:
#   x-ratelimit-policy: "50;w=1, 0;w=2678400"     (per-second LIMIT;window, monthly LIMIT;window)
#   x-ratelimit-remaining: "49, 0"                (per-second remaining, monthly remaining)
#   x-ratelimit-reset: "1, 1296193"               (seconds to reset for each bucket)
#
# IMPORTANT: a LIMIT of 0 in x-ratelimit-policy means "no cap"; the
# matching remaining value of 0 in that case is not "exhausted". We
# only treat a bucket as exhausted when its POLICY LIMIT is positive.
RATE_POLICY=$(grep -i "^x-ratelimit-policy:" /tmp/brave_headers | head -1 | sed 's/^[^:]*: //' | tr -d '\r')
RATE_REMAINING=$(grep -i "^x-ratelimit-remaining:" /tmp/brave_headers | head -1 | sed 's/^[^:]*: //' | tr -d '\r')
RATE_RESET=$(grep -i "^x-ratelimit-reset:" /tmp/brave_headers | head -1 | sed 's/^[^:]*: //' | tr -d '\r')

# Extract per-bucket limits (the integer before ';w=' in policy)
PER_SEC_LIMIT=$(echo "$RATE_POLICY" | awk -F',' '{print $1}' | awk -F';' '{print $1}' | tr -d ' ')
MONTHLY_LIMIT=$(echo "$RATE_POLICY" | awk -F',' '{print $2}' | awk -F';' '{print $1}' | tr -d ' ')
PER_SEC_REMAINING=$(echo "$RATE_REMAINING" | awk -F',' '{print $1}' | tr -d ' ')
MONTHLY_REMAINING=$(echo "$RATE_REMAINING" | awk -F',' '{print $2}' | tr -d ' ')

# Count results in the response body
RESULTS_COUNT=$(python3 -c "
import json, sys
try:
    d = json.load(open('$TMP'))
    print(len(d.get('web', {}).get('results', [])))
except Exception:
    print(0)
")

rm -f "$TMP" /tmp/brave_headers

STATUS="ok"
REASON=""

# Monthly bucket: only flag if the policy says a cap exists (>0) AND remaining is at/near zero.
if [[ "${MONTHLY_LIMIT:-0}" -gt 0 ]]; then
  if [[ "${MONTHLY_REMAINING:-0}" -le 0 ]]; then
    STATUS="unhealthy"
    REASON="monthly quota exhausted (limit=$MONTHLY_LIMIT remaining=$MONTHLY_REMAINING)"
  elif [[ "${MONTHLY_REMAINING:-0}" -lt 100 ]]; then
    STATUS="degraded"
    REASON="monthly quota low ($MONTHLY_REMAINING/$MONTHLY_LIMIT remaining)"
  fi
fi

# Per-second bucket: a brief 0 remaining isn't catastrophic (resets in 1s)
# but warning-worthy if combined with a low results count.
if [[ "$STATUS" == "ok" && "${RESULTS_COUNT:-0}" -lt 3 ]]; then
  STATUS="degraded"
  REASON="only $RESULTS_COUNT results returned (expected 3)"
fi

if [[ -n "$QUIET" && "$STATUS" == "ok" ]]; then
  exit 0
fi

echo "status: $STATUS"
[[ -n "$REASON" ]] && echo "reason: $REASON"
echo "ratelimit_remaining: $RATE_REMAINING"
echo "ratelimit_reset_seconds: $RATE_RESET"
echo "results_count: $RESULTS_COUNT"

case "$STATUS" in
  ok)         exit 0;;
  degraded)   exit 1;;
  unhealthy)  exit 2;;
esac
