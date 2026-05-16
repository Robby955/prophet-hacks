#!/usr/bin/env bash
# Start the FastAPI forecast agent + cloudflared tunnel, then re-register
# the (new) public URL with Prophet Arena.
#
# Idempotent: safe to re-run. Kills any prior agent/tunnel first.
#
# Usage:
#   bash scripts/start_agent.sh
#   bash scripts/start_agent.sh ensemble_logit   # use a different variant
#
# Logs:
#   /tmp/oracles_server.log     uvicorn output
#   /tmp/oracles_tunnel.log     cloudflared output (contains the public URL)

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd -P)"
VENV="$REPO_ROOT/.venv"
VARIANT="${1:-${PROPHET_AGENT_VARIANT:-single_llm}}"

echo ">> killing any prior agent/tunnel"
pkill -f forecast_agent_server.py 2>/dev/null || true
pkill -f "cloudflared tunnel --url http://localhost:8000" 2>/dev/null || true
sleep 1

echo ">> starting FastAPI server (variant=$VARIANT)"
PROPHET_AGENT_VARIANT="$VARIANT" \
  nohup "$VENV/bin/python" forecast_agent_server.py \
  > /tmp/oracles_server.log 2>&1 &
SERVER_PID=$!
echo "   server pid=$SERVER_PID"

echo ">> waiting for server health"
for i in {1..15}; do
  if curl -sf http://localhost:8000/healthz > /dev/null; then
    echo "   server healthy"
    break
  fi
  sleep 1
  if [[ $i -eq 15 ]]; then
    echo "   ERROR: server did not become healthy in 15s; see /tmp/oracles_server.log" >&2
    tail -20 /tmp/oracles_server.log >&2
    exit 1
  fi
done

echo ">> starting cloudflared tunnel"
nohup /opt/homebrew/bin/cloudflared tunnel --url http://localhost:8000 \
  > /tmp/oracles_tunnel.log 2>&1 &
TUNNEL_PID=$!
echo "   tunnel pid=$TUNNEL_PID"

echo ">> waiting for tunnel URL"
TUNNEL_URL=""
for i in {1..30}; do
  TUNNEL_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/oracles_tunnel.log | head -1 || true)"
  if [[ -n "$TUNNEL_URL" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$TUNNEL_URL" ]]; then
  echo "ERROR: no tunnel URL after 30s; see /tmp/oracles_tunnel.log" >&2
  tail -20 /tmp/oracles_tunnel.log >&2
  exit 1
fi

echo "   public URL: $TUNNEL_URL"

echo ">> verifying tunnel reachability"
for i in {1..15}; do
  if curl -sf "$TUNNEL_URL/healthz" > /dev/null; then
    echo "   public URL healthy"
    break
  fi
  sleep 2
done

echo ">> registering endpoint with Prophet Arena"
"$VENV/bin/prophet" forecast register \
  --team-name CanadaHacks \
  --endpoint-url "$TUNNEL_URL/predict"

echo ""
echo ">> DONE. Agent is live."
echo "   variant:      $VARIANT"
echo "   public URL:   $TUNNEL_URL/predict"
echo "   server pid:   $SERVER_PID"
echo "   tunnel pid:   $TUNNEL_PID"
echo "   server logs:  /tmp/oracles_server.log"
echo "   tunnel logs:  /tmp/oracles_tunnel.log"
echo ""
echo "Check the leaderboard: $VENV/bin/prophet forecast leaderboard"
