#!/usr/bin/env bash
# Single safe path to Railway deploy. Use this instead of raw `railway up`.
#
# Runs preflight (tests, upload-size sanity, push check), writes the current
# commit SHA to a non-secret Railway variable so /healthz can surface it, then
# kicks off railway up.
#
# Usage: ./scripts/agent/deploy.sh [optional-message]
#
# 2026-05-16 incident this prevents: deployed bloat broke three uploads
# (.claude/worktrees was 18MB and got included). Preflight catches that
# at step 4.

set -euo pipefail

cd "$(dirname "$0")/../.."
REPO_ROOT="$(pwd -P)"

MSG="${1:-deploy via scripts/agent/deploy.sh}"

# Run preflight (exits non-zero on failure)
"$REPO_ROOT/scripts/preflight.sh"

# Pin commit SHA into the Railway runtime metadata. `railway up` file uploads
# do not reliably expose a git commit SHA, and gitignored `.commit_sha` files
# do not survive the upload filter.
SHA=$(git rev-parse --short=8 HEAD)
RAILWAY_CALLER="skill:use-railway@1.2.1" RAILWAY_AGENT_SESSION="deploy-var-$(date +%s)" \
  railway variable set PROPHET_BUILD_COMMIT_SHA="$SHA" \
    --service oracles-agent --environment production > /dev/null

echo ""
echo "=== triggering railway up (detached) for $SHA ==="
SESSION="deploy-$(date +%s)"
RAILWAY_CALLER="skill:use-railway@1.2.1" RAILWAY_AGENT_SESSION="$SESSION" \
  railway up --service oracles-agent --environment production --detach \
    -m "$MSG" 2>&1 | tail -10

echo ""
echo "=== watch with: railway deployment list --service oracles-agent --json | head ==="
echo "=== verify with: curl -s https://agent.forecastingpath.com/healthz ==="
echo "=== expect /healthz commit to equal: $SHA"
