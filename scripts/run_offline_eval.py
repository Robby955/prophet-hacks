#!/usr/bin/env python3
"""Top-level offline-eval orchestrator.

Runs the no-leakage gate, then the 8-variant evaluation, then writes a
status snapshot. Friday-evening dry-run target:

    PROPHET_OFFLINE_MOCK=1 python scripts/run_offline_eval.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.evaluate_offline import run_eval  # noqa: E402


def main() -> int:
    dataset = ROOT / "offline" / "sample_tasks.jsonl"
    out_dir = ROOT / "reports"
    if not os.getenv("PROPHET_OFFLINE_MOCK"):
        print(
            "PROPHET_OFFLINE_MOCK not set. Stub mocks are still used in this build; "
            "real-LLM mode will be enabled when forecaster.py providers are wired in v3.x."
        )
    html_path = run_eval(dataset, out_dir)
    print(f"Wrote {html_path}")
    print("Update reports/status.md with the top-line numbers (or run `make status`).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
