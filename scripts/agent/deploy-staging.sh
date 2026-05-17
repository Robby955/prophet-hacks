#!/usr/bin/env bash
# Staging deploy. Same runtime bundle as production deploy.sh, but targets
# the Railway 'staging' environment + 'oracles-agent-stage' service.
#
# Usage:
#   git checkout staging   # MUST be on staging branch; main is for production
#   ./scripts/agent/deploy-staging.sh [optional-message]
#
# Differences from deploy.sh:
# - Targets service=oracles-agent-stage  environment=staging
# - Allows running from the staging branch (deploy.sh's preflight requires
#   HEAD = origin/main; we relax that for staging)
# - Verify gate still runs (tests must pass)
# - Working tree still must be clean
#
# Staging environment is the safe place to land any prompt or front-end
# change before promoting to production via `git checkout main && git
# merge --ff-only staging && ./scripts/agent/deploy.sh`.

set -euo pipefail

cd "$(dirname "$0")/../.."
REPO_ROOT="$(pwd -P)"

MSG="${1:-deploy via deploy-staging.sh}"

# Guard: must be on staging branch (or any non-main feature branch). Refuse
# to deploy main to staging — that's a sign of confusion.
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" == "main" ]]; then
  echo "ERR: refusing to deploy 'main' to staging. Use deploy.sh for production." >&2
  exit 1
fi
echo "=== staging deploy from branch '$CURRENT_BRANCH' ==="

# Tests must pass
echo "[1/3] verify gate"
if ! PATH="$REPO_ROOT/.venv/bin:$PATH" "$REPO_ROOT/scripts/agent/verify.sh" > /tmp/staging_verify.log 2>&1; then
  tail -20 /tmp/staging_verify.log >&2
  echo "ERR: verify.sh did not pass; see /tmp/staging_verify.log" >&2
  exit 1
fi
echo "  ok: verify green"

# Working tree clean
echo "[2/3] working tree clean"
if ! git diff --quiet HEAD 2>/dev/null; then
  git status --short >&2
  echo "ERR: uncommitted changes; commit or stash" >&2
  exit 1
fi
echo "  ok: clean"

SHA=$(git rev-parse --short=8 HEAD)
echo "[3/3] deploy plan: $SHA -> staging"

# Build runtime bundle (same content as production)
BUNDLE_DIR="$(mktemp -d /tmp/prophet-staging.XXXXXX)"
cleanup() { rm -rf "$BUNDLE_DIR"; }
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

# Pin SHA so /healthz.commit reflects the staging build
RAILWAY_CALLER="skill:use-railway-staging@1.0.0" RAILWAY_AGENT_SESSION="stage-deploy-var-$(date +%s)" \
  railway variable set PROPHET_BUILD_COMMIT_SHA="$SHA" \
    --service oracles-agent-stage --environment staging > /dev/null 2>&1 || \
  echo "  warn: could not set PROPHET_BUILD_COMMIT_SHA (service may not exist yet; will be set on first deploy)"

echo ""
echo "=== triggering railway up (detached) for $SHA -> staging ==="
SESSION="stage-deploy-$(date +%s)"
RAILWAY_CALLER="skill:use-railway-staging@1.0.0" RAILWAY_AGENT_SESSION="$SESSION" \
  railway up "$BUNDLE_DIR" --path-as-root \
    --service oracles-agent-stage --environment staging --detach \
    -m "$MSG" 2>&1 | tail -10

echo ""
echo "=== watch staging deploys with: railway deployment list --service oracles-agent-stage --environment staging --json | head ==="
echo "=== once domain is generated, verify with: curl -s <staging-domain>/healthz"
