#!/usr/bin/env bash
# Post-event orchestrator. Run this after Prophet Arena publishes
# resolved outcomes for the live eval window. Produces:
#
#   reports/<stamp>/analysis.json        — Brier/BSS/ECE/Murphy per-event
#   reports/<stamp>/predictions.json     — copy of what /predictions returned
#   reports/<stamp>/summary.html         — refreshed visual summary report
#   reports/<stamp>/decisions_excerpt.md — DECISIONS.md timestamp window
#   reports/<stamp>/retrospective.md     — auto-populated template
#
# Usage:
#   ./scripts/post_event_orchestrator.sh \
#       --actuals path/to/live_actuals.json
#
#   ./scripts/post_event_orchestrator.sh \
#       --actuals path/to/live_actuals.json \
#       --token "$DASHBOARD_AUTH_TOKEN"
#
# The actuals.json should be {ticker: winning_outcome_label} or
# {ticker: 1.0/0.0} for binary. See scripts/analyze_results.py for
# the supported shapes.

set -euo pipefail

cd "$(dirname "$0")/.."

ACTUALS=""
TOKEN="${DASHBOARD_AUTH_TOKEN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --actuals)  ACTUALS="$2"; shift 2;;
    --token)    TOKEN="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

if [[ -z "$ACTUALS" ]]; then
  echo "usage: $0 --actuals <path> [--token <dashboard-token>]" >&2
  exit 1
fi
if [[ ! -f "$ACTUALS" ]]; then
  echo "actuals file not found: $ACTUALS" >&2
  exit 1
fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="reports/post_event_$STAMP"
mkdir -p "$OUT"
echo "=== post-event run $STAMP -> $OUT ==="

echo "[1/4] pull live /predictions snapshot"
PRED_URL="https://agent.forecastingpath.com/predictions"
if [[ -n "$TOKEN" ]]; then
  curl -s --max-time 30 -H "x-dashboard-token: $TOKEN" "$PRED_URL" > "$OUT/predictions.json"
else
  echo "  no DASHBOARD_AUTH_TOKEN; trying public path (will likely 401)"
  curl -s --max-time 30 "$PRED_URL" > "$OUT/predictions.json"
fi
PRED_COUNT=$(python3 -c "import json; print(json.load(open('$OUT/predictions.json')).get('count', '?'))")
echo "  saved $PRED_COUNT predictions"

echo "[2/4] score predictions vs actuals"
.venv/bin/python scripts/analyze_results.py \
  --predictions "$OUT/predictions.json" \
  --actuals "$ACTUALS" \
  --out "$OUT/" 2>&1 | tail -10

echo "[3/4] rebuild static summary report with new data"
# Concatenate live predictions into the existing prediction directory so the
# summary report can include them. Non-destructive copy.
cp "$OUT/predictions.json" "$OUT/predictions_snapshot.json"
.venv/bin/python scripts/build_summary_report.py --out "$OUT/" 2>&1 | tail -5

echo "[4/4] generate retrospective draft from template"
TEMPLATE="docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md"
RETRO="$OUT/retrospective.md"
if [[ -f "$TEMPLATE" ]]; then
  cp "$TEMPLATE" "$RETRO"
  # Replace any obvious template placeholders with concrete values
  sed -i.bak "s/<INSERT_DATE>/$(date -u +%Y-%m-%d)/g" "$RETRO" 2>/dev/null || true
  sed -i.bak "s/<INSERT_COMMIT>/$(git rev-parse --short=8 HEAD)/g" "$RETRO" 2>/dev/null || true
  rm -f "$RETRO.bak"
fi

# Pull DECISIONS.md entries from today and onward as a contextual excerpt
echo "## DECISIONS.md entries dated 2026-05-16 or later" > "$OUT/decisions_excerpt.md"
echo "" >> "$OUT/decisions_excerpt.md"
awk '/^## 2026-05/{capture=1} capture' docs/DECISIONS.md >> "$OUT/decisions_excerpt.md" || true

echo ""
echo "=== done ==="
echo "  artifacts: $OUT/"
ls -la "$OUT/" | tail -10
echo ""
echo "Next: review $OUT/retrospective.md, finalize, then append to"
echo "docs/POST_EVENT_RETROSPECTIVE.md (or wherever the canonical lives)."
