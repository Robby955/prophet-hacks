#!/usr/bin/env bash
# Pre-deploy gate. Run before any railway up.
#
# Catches the failure modes that bit us 2026-05-16:
#   - tests/smoke fail (would deploy broken code)
#   - upload tarball > 10MB (worktree bloat broke 3 deploys before we
#     figured out .claude/ was in the upload)
#   - working tree dirty or untracked files (would deploy code/files that
#     don't match main)
#   - local HEAD not pushed (would deploy code not on GitHub)
#   - deployed SHA already matches local HEAD (no-op deploy)
#
# Exits 0 if safe to deploy, non-zero with an explanation otherwise.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd -P)"
PY="$REPO_ROOT/.venv/bin/python"

fail() { echo "PREFLIGHT FAIL: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }
ok()   { echo "  ok: $*"; }

echo "=== preflight for railway deploy ==="

# 1. Verify gate
echo "[1/5] verify gate (tests + smoke import + dry-run)"
if ! PATH="$REPO_ROOT/.venv/bin:$PATH" "$REPO_ROOT/scripts/agent/verify.sh" > /tmp/preflight_verify.log 2>&1; then
  tail -20 /tmp/preflight_verify.log >&2
  fail "verify.sh did not pass; see /tmp/preflight_verify.log"
fi
ok "verify green"

# 2. Working tree clean
echo "[2/5] working tree status"
if ! git diff --quiet HEAD 2>/dev/null; then
  git status --short >&2
  fail "uncommitted changes; commit or stash before deploying"
fi
if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  git status --short >&2
  fail "untracked files would be uploaded; commit them or add them to .gitignore"
fi
ok "working tree clean"

# 3. HEAD pushed to origin
echo "[3/5] HEAD vs origin"
git fetch origin --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "")
if [[ -z "$REMOTE" ]]; then
  warn "no origin/main; skipping push check"
elif [[ "$LOCAL" != "$REMOTE" ]]; then
  fail "HEAD ($LOCAL) != origin/main ($REMOTE); push first"
fi
ok "HEAD = origin/main = $(git rev-parse --short HEAD)"

# 4. Upload size sanity (sum of tracked files)
echo "[4/5] upload size (what railway up would send)"
# Use git ls-files which respects .gitignore. Step 2 rejects untracked files.
TRACKED_KB=$(git ls-files -z | xargs -0 du -k 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
TOTAL_KB=$TRACKED_KB
TOTAL_MB=$((TOTAL_KB / 1024))
if [[ "$TOTAL_MB" -gt 10 ]]; then
  echo "  upload size: ${TOTAL_MB}MB (${TOTAL_KB}KB)" >&2
  fail "upload would be >10MB; add bloat to .gitignore (this is exactly the bug from 2026-05-16)"
fi
ok "upload size ${TOTAL_MB}MB (${TRACKED_KB}KB tracked)"

# 5. Show what would actually deploy
echo "[5/5] deploy plan"
SHORT=$(git rev-parse --short=8 HEAD)
SUBJECT=$(git log -1 --format='%s')
ok "would deploy: $SHORT  -  $SUBJECT"

# Also surface the currently-deployed SHA on Railway if curl + jq are around
if command -v curl > /dev/null; then
  LIVE_SHA=$(curl -s --max-time 5 https://agent.forecastingpath.com/healthz 2>/dev/null | \
             python3 -c "import json,sys; print(json.load(sys.stdin).get('commit','?'))" 2>/dev/null || echo "?")
  if [[ "$LIVE_SHA" != "?" && "$LIVE_SHA" != "dev" ]]; then
    echo "  currently live: $LIVE_SHA"
    if [[ "$LIVE_SHA" == "$SHORT" ]]; then
      warn "live SHA matches local HEAD; this deploy is a no-op (skip unless you've changed env vars)"
    fi
  fi
fi

echo "=== preflight OK ==="
