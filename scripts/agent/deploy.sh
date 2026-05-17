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

SHA=$(git rev-parse --short=8 HEAD)

# Build a minimal runtime bundle. Full-repo `railway up` has repeatedly failed
# with code-snapshot/TLS upload errors even when preflight reports a small
# tracked size. The server only needs these runtime files, static assets, and
# dashboard comparison data.
BUNDLE_DIR="$(mktemp -d /tmp/prophet-runtime.XXXXXX)"
cleanup() {
  rm -rf "$BUNDLE_DIR"
}
trap cleanup EXIT

copy_file() {
  local src="$1"
  if [[ -f "$src" ]]; then
    mkdir -p "$BUNDLE_DIR/$(dirname "$src")"
    cp "$src" "$BUNDLE_DIR/$src"
  fi
}

copy_dir() {
  local src="$1"
  if [[ -d "$src" ]]; then
    mkdir -p "$BUNDLE_DIR/$(dirname "$src")"
    cp -R "$src" "$BUNDLE_DIR/$(dirname "$src")/"
  fi
}

copy_file ".python-version"
copy_file "railway.toml"
copy_file "requirements.txt"
copy_file "config.yaml"
copy_file "forecast_agent_server.py"
copy_file "forecast_track.py"
copy_file "chat_completions_adapter.py"
copy_dir "evaluation"
copy_dir "forecasting"
copy_dir "static"
mkdir -p "$BUNDLE_DIR/data"
copy_file "data/resolved.json"
copy_file "data/actuals.json"
copy_dir "data/datasets"
copy_dir "data/predictions"

echo ""
echo "=== runtime bundle ==="
du -sh "$BUNDLE_DIR"

# Pin commit SHA into the Railway runtime metadata. `railway up` file uploads
# do not reliably expose a git commit SHA, and gitignored `.commit_sha` files
# do not survive the upload filter.
RAILWAY_CALLER="skill:use-railway@1.2.1" RAILWAY_AGENT_SESSION="deploy-var-$(date +%s)" \
  railway variable set PROPHET_BUILD_COMMIT_SHA="$SHA" \
    --service oracles-agent --environment production > /dev/null

echo ""
echo "=== triggering railway up (detached) for $SHA ==="
SESSION="deploy-$(date +%s)"
RAILWAY_CALLER="skill:use-railway@1.2.1" RAILWAY_AGENT_SESSION="$SESSION" \
  railway up "$BUNDLE_DIR" --path-as-root \
    --service oracles-agent --environment production --detach \
    -m "$MSG" 2>&1 | tail -10

echo ""
echo "=== watch with: railway deployment list --service oracles-agent --json | head ==="
echo "=== verify with: curl -s https://agent.forecastingpath.com/healthz ==="
echo "=== expect /healthz commit to equal: $SHA"
