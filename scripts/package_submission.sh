#!/usr/bin/env bash
# Package the submission zip. Run from repo root.
set -euo pipefail

OUT="prophet-hacks-submission.zip"
rm -f "$OUT"

zip -r "$OUT" . \
  -x ".venv/*" \
  -x "__pycache__/*" \
  -x "*.pyc" \
  -x "logs/*" \
  -x "trace/*" \
  -x ".env" \
  -x ".env.*" \
  -x "!.env.example" \
  -x ".git/*" \
  -x ".pytest_cache/*" \
  -x ".mypy_cache/*" \
  -x ".ruff_cache/*" \
  -x "reports/*" \
  -x "test-results/*" \
  -x ".DS_Store" \
  -x "scripts/__pycache__/*"

SHA=$(shasum -a 256 "$OUT" | cut -d' ' -f1)
SIZE=$(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT" 2>/dev/null)

echo ""
echo "Submission package: $OUT"
echo "SHA-256: $SHA"
echo "Size: $SIZE bytes"
echo ""
echo "Verify with:"
echo "  mkdir /tmp/verify-submission && cd /tmp/verify-submission"
echo "  unzip $(pwd)/$OUT"
echo "  python -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python agent.py --slug verify-smoke --dry-run"
