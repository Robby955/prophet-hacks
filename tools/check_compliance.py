#!/usr/bin/env python3
"""Pre-submission compliance gate.

Run before zipping a submission. Refuses to pass if any item below
is missing. Designed to be cheap (no LLM calls, no network).

Run: `python tools/check_compliance.py` or `make compliance`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = [
    "agent.py",
    "forecaster.py",
    "risk.py",
    "logger.py",
    "config.yaml",
    "requirements.txt",
    "README.md",
    "docs/RUNBOOK.md",
    "docs/PRE_EVENT_CHECKLIST.md",
    "docs/DECISIONS.md",
    "scripts/agent/verify.sh",
]

FORBIDDEN_IN_REPO = [
    ".env",
    "logs/",
]

BANNED_PHRASES = ["delve", "leverage", "navigate", "embark", "tapestry", "realm"]


def check_required_files() -> List[Tuple[str, str]]:
    issues = []
    for f in REQUIRED_FILES:
        if not (ROOT / f).exists():
            issues.append(("missing-required-file", f))
    return issues


def check_no_hardcoded_keys() -> List[Tuple[str, str]]:
    issues = []
    suspicious = re.compile(
        r"(sk-[a-zA-Z0-9_-]{20,}|sk_live_[a-zA-Z0-9]{20,}|"
        r"AKIA[0-9A-Z]{16}|ghp_[a-zA-Z0-9]{20,})"
    )
    for p in ROOT.rglob("*.py"):
        # Skip .venv, .git
        if any(part in p.parts for part in (".venv", ".git", "node_modules")):
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if suspicious.search(text):
            issues.append(("hardcoded-key", str(p.relative_to(ROOT))))
    return issues


def check_pinned_versions() -> List[Tuple[str, str]]:
    issues = []
    req = ROOT / "requirements.txt"
    if not req.exists():
        return [("no-requirements-file", "")]
    for line in req.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if "==" not in line and ">=" not in line and "~=" not in line:
            issues.append(("unpinned-dep", line))
    return issues


def check_risk_caps_declared() -> List[Tuple[str, str]]:
    """The risk.py module must expose EDGE_THRESHOLD, MAX_TRADES_PER_TICK,
    MAX_NOTIONAL_PER_NEW_POSITION, MAX_OPEN_POSITIONS as module-level constants.
    Locked by the strategy memo."""
    risk_path = ROOT / "risk.py"
    if not risk_path.exists():
        return [("missing-risk-py", "")]
    text = risk_path.read_text()
    required = [
        "EDGE_THRESHOLD",
        "MAX_TRADES_PER_TICK",
        "MAX_NOTIONAL_PER_NEW_POSITION",
        "MAX_OPEN_POSITIONS",
    ]
    return [("missing-risk-constant", r) for r in required if r not in text]


def check_no_forbidden_files() -> List[Tuple[str, str]]:
    issues = []
    for f in FORBIDDEN_IN_REPO:
        candidate = ROOT / f
        if candidate.exists():
            issues.append(("would-leak-into-submission", f))
    return issues


def check_no_banned_phrases() -> List[Tuple[str, str]]:
    issues = []
    for p in list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.py")):
        if any(part in p.parts for part in (".venv", ".git", "node_modules", "__pycache__")):
            continue
        try:
            text = p.read_text(errors="ignore").lower()
        except OSError:
            continue
        for phrase in BANNED_PHRASES:
            # Word-boundary matches only
            if re.search(rf"\b{phrase}\b", text):
                issues.append(("banned-phrase", f"{p.relative_to(ROOT)}: {phrase}"))
    return issues


def check_author_email() -> List[Tuple[str, str]]:
    issues = []
    try:
        result = subprocess.run(
            ["git", "config", "--local", "--get", "user.email"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        email = (result.stdout or "").strip()
        if email and email != "robbysneiderman@gmail.com":
            issues.append(("wrong-author-email", email))
    except FileNotFoundError:
        issues.append(("git-not-found", ""))
    return issues


def main() -> int:
    checks = [
        ("required-files", check_required_files()),
        ("hardcoded-keys", check_no_hardcoded_keys()),
        ("pinned-versions", check_pinned_versions()),
        ("risk-constants", check_risk_caps_declared()),
        ("forbidden-files", check_no_forbidden_files()),
        ("banned-phrases", check_no_banned_phrases()),
        ("author-email", check_author_email()),
    ]
    total = 0
    for name, issues in checks:
        if not issues:
            print(f"[OK]    {name}")
            continue
        for kind, detail in issues:
            total += 1
            print(f"[FAIL]  {name}: {kind} {detail}")
    if total:
        print(f"\nCompliance gate FAILED with {total} issue(s).")
        return 1
    print("\nCompliance gate PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
