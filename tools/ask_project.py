#!/usr/bin/env python3
"""ask_project.py — CLI RAG over three separate knowledge bases.

Per the v6 playbook Section 11, we keep three KBs **separate** to
avoid leakage and stale reasoning contaminating live evidence:

  ops      — README, runbooks, CI summaries, status.md, errors
  research — Prophet Arena paper, Kalshi paper, BORROWED_STRENGTH.md
  evidence — timestamped retrieved sources keyed by event_id/market_id

The interface is plain CLI:

    python tools/ask_project.py "What is the current default variant?"
    python tools/ask_project.py --kb research "Kalshi longshot rule"
    python tools/ask_project.py --kb evidence "BTC > 100k by Dec"

This v0 uses lexical TF-IDF retrieval over Markdown files. It is
intentionally simple — no vector DB, no external API. v1 can swap
in BM25 or embeddings without changing the CLI shape.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple


ROOT = Path(__file__).resolve().parent.parent

KB_PATHS = {
    "ops": [
        ROOT / "README.md",
        ROOT / "docs" / "RUNBOOK.md",
        ROOT / "docs" / "DECISIONS.md",
        ROOT / "docs" / "PRE_EVENT_CHECKLIST.md",
        ROOT / "docs" / "MONITOR.md",
        ROOT / "reports" / "status.md",
        ROOT / "agent_protocol.md",
    ],
    "research": [
        ROOT / "docs" / "KALSHI_FINDINGS.md",
        ROOT / "docs" / "BORROWED_STRENGTH.md",
        ROOT / "docs" / "V3_OFFLINE_HARNESS.md",
        ROOT / "docs" / "ARCHITECTURE_V2.md",
    ],
    "evidence": [
        # Future: per-market timestamped evidence files. Stub for now.
    ],
}


# -- Tokenization + TF-IDF ------------------------------------------------


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "is",
        "are", "be", "by", "with", "as", "at", "this", "that", "we", "it",
        "from", "but", "not", "should", "would", "can", "has", "have",
    }
)


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOPWORDS]


@dataclass
class Doc:
    path: Path
    text: str
    tokens: List[str]
    counts: Counter


def load_docs(kb: str) -> List[Doc]:
    paths = KB_PATHS.get(kb, [])
    docs = []
    for p in paths:
        if not p.exists():
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        toks = tokenize(text)
        if not toks:
            continue
        docs.append(Doc(path=p, text=text, tokens=toks, counts=Counter(toks)))
    return docs


def _idf(docs: List[Doc], term: str) -> float:
    if not docs:
        return 0.0
    df = sum(1 for d in docs if term in d.counts)
    if df == 0:
        return 0.0
    return math.log(1.0 + (len(docs) - df + 0.5) / (df + 0.5))


def rank(query: str, docs: List[Doc], top_k: int = 4) -> List[Tuple[float, Doc]]:
    q_toks = tokenize(query)
    if not q_toks or not docs:
        return []
    idf_by_term = {t: _idf(docs, t) for t in q_toks}
    scored = []
    for d in docs:
        score = 0.0
        for t in q_toks:
            tf = d.counts.get(t, 0)
            if tf == 0:
                continue
            # BM25-lite: tf-saturation + length-normalization
            avg_len = max(1.0, sum(len(x.tokens) for x in docs) / len(docs))
            norm = tf * 2.0 / (tf + 1.0 * (0.5 + 0.5 * len(d.tokens) / avg_len))
            score += idf_by_term[t] * norm
        if score > 0:
            scored.append((score, d))
    scored.sort(reverse=True, key=lambda x: x[0])
    return scored[:top_k]


def best_snippet(query: str, text: str, max_len: int = 600) -> str:
    """Return the contiguous chunk of `text` with the densest match."""
    q_toks = set(tokenize(query))
    paragraphs = re.split(r"\n\s*\n", text)
    best_score = -1.0
    best_para = ""
    for para in paragraphs:
        toks = tokenize(para)
        if not toks:
            continue
        hits = sum(1 for t in toks if t in q_toks)
        score = hits / (1 + len(toks) ** 0.5)
        if score > best_score:
            best_score = score
            best_para = para
    snippet = best_para.strip()
    if len(snippet) > max_len:
        snippet = snippet[: max_len - 3] + "..."
    return snippet or text[:max_len]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument(
        "--kb",
        choices=("ops", "research", "evidence", "all"),
        default="all",
    )
    ap.add_argument("--top-k", type=int, default=3)
    args = ap.parse_args()
    query = " ".join(args.query)

    kbs = ("ops", "research", "evidence") if args.kb == "all" else (args.kb,)
    any_hits = False
    for kb in kbs:
        docs = load_docs(kb)
        results = rank(query, docs, top_k=args.top_k)
        if not results:
            continue
        any_hits = True
        print(f"\n=== {kb.upper()} ===")
        for score, doc in results:
            rel_path = doc.path.relative_to(ROOT)
            print(f"\n• {rel_path}  (score {score:.2f})")
            print("  " + best_snippet(query, doc.text).replace("\n", "\n  "))

    if not any_hits:
        print(f"No hits for: {query}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
