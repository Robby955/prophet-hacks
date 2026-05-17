#!/usr/bin/env python3
"""Murphy (1973) Brier-score decomposition: BS = REL - RES + UNC.

Brier = Reliability − Resolution + Uncertainty.

- **Reliability** (lower better): how well calibrated the forecaster's stated
  probabilities are. For each bin of `p_yes`, the average of `p_yes` should
  equal the empirical hit rate. If not, the gap squared (weighted by bin
  size) accumulates as reliability error.
- **Resolution** (higher better): how much the forecaster's bin-conditional
  hit rates differ from the unconditional base rate. A forecaster that
  separates winners from losers (puts high probability on winners and low on
  losers) has high resolution.
- **Uncertainty** (irreducible): the variance of outcomes themselves,
  `base_rate * (1 - base_rate)`. The forecaster cannot beat this.

Run:
    python scripts/murphy_decomposition.py
prints a table over all 5 model variants for the 26 resolved events.

Writes a Markdown snippet to `data/predictions/murphy_decomposition.md`
and JSON to `data/predictions/murphy_decomposition.json` that the summary
report builder can pick up.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
PRED = DATA / "predictions"

VARIANTS = [
    ("Opus 4.7 (production)", PRED / "multi_outcome_retrieval.json"),
    ("Opus 4.6", PRED / "ablation_claude-opus-4-6.json"),
    ("GPT-5.2", PRED / "ablation_gpt-5-2.json"),
    ("GPT-5.5", PRED / "ablation_gpt-5-5.json"),
    ("Gemini 3.1 Pro", PRED / "ablation_gemini-3-1-pro-preview.json"),
]

# 10 bins is standard for n=26. With 26 events some bins will be empty.
BIN_EDGES = [0.0, 0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 1.001]


def bin_index(p: float) -> int:
    for i in range(len(BIN_EDGES) - 1):
        if BIN_EDGES[i] <= p < BIN_EDGES[i + 1]:
            return i
    return len(BIN_EDGES) - 2


def decompose(preds: list[tuple[float, int]]) -> dict[str, float]:
    """preds: list of (p_yes, actual_0_or_1) tuples. Returns REL, RES, UNC, BS."""
    n = len(preds)
    if n == 0:
        return {"REL": 0.0, "RES": 0.0, "UNC": 0.0, "BS": 0.0, "n": 0}

    base_rate = sum(y for _, y in preds) / n
    unc = base_rate * (1 - base_rate)

    # Group by bin.
    bins: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, y in preds:
        bins[bin_index(p)].append((p, y))

    rel = 0.0
    res = 0.0
    for _, items in bins.items():
        nk = len(items)
        pbar_k = sum(p for p, _ in items) / nk  # mean forecast in bin
        ybar_k = sum(y for _, y in items) / nk  # empirical hit rate in bin
        rel += (nk / n) * (pbar_k - ybar_k) ** 2
        res += (nk / n) * (ybar_k - base_rate) ** 2

    bs_check = sum((p - y) ** 2 for p, y in preds) / n
    return {
        "REL": round(rel, 5),
        "RES": round(res, 5),
        "UNC": round(unc, 5),
        "BS_decomp": round(rel - res + unc, 5),
        "BS_direct": round(bs_check, 5),
        "base_rate": round(base_rate, 4),
        "n": n,
        "n_bins_used": len(bins),
    }


def main() -> int:
    actuals = json.load(open(DATA / "actuals.json"))
    rows: list[dict] = []

    for label, path in VARIANTS:
        if not path.exists():
            continue
        raw = json.loads(path.read_text())
        preds_rows = raw.get("predictions") if isinstance(raw, dict) else raw
        pairs: list[tuple[float, int]] = []
        for r in preds_rows:
            t = r.get("market_ticker") or r.get("event_ticker")
            if t not in actuals:
                continue
            pairs.append((float(r["p_yes"]), int(actuals[t])))
        d = decompose(pairs)
        d["variant"] = label
        rows.append(d)

    # Print table.
    print(f"{'Variant':<30} {'n':>3} {'REL ↓':>8} {'RES ↑':>8} {'UNC':>8} {'BS':>8}")
    for r in rows:
        print(
            f"{r['variant']:<30} {r['n']:>3} {r['REL']:>8.5f} {r['RES']:>8.5f} "
            f"{r['UNC']:>8.5f} {r['BS_direct']:>8.5f}"
        )
    # Cross-check.
    print()
    print("Sanity: REL - RES + UNC should equal BS:")
    for r in rows:
        gap = abs(r["BS_decomp"] - r["BS_direct"])
        print(f"  {r['variant']}: |decomp - direct| = {gap:.6f}")

    # Write outputs.
    out_json = PRED / "murphy_decomposition.json"
    out_json.write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {out_json}")

    out_md = PRED / "murphy_decomposition.md"
    lines = [
        "## Brier decomposition (Murphy 1973) — production vs alternatives",
        "",
        "Brier = Reliability − Resolution + Uncertainty.",
        "",
        "- **REL ↓** (reliability error): how miscalibrated bin-conditional",
        "  forecasts are from empirical bin hit-rates.",
        "- **RES ↑** (resolution): how much bin-conditional hit-rates differ",
        "  from the base rate — measures the forecaster's discriminative power.",
        "- **UNC** (irreducible): base-rate variance `p(1-p)`. Same across variants",
        "  on the same dataset (here 0.246).",
        "",
        "| Variant | n | REL ↓ | RES ↑ | UNC | BS |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['variant']} | {r['n']} | {r['REL']:.5f} | "
            f"{r['RES']:.5f} | {r['UNC']:.5f} | {r['BS_direct']:.5f} |"
        )
    lines.append("")
    lines.append(
        "Reading the decomposition: low REL means the forecaster's stated "
        "probabilities track empirical hit rates within bins. High RES means "
        "the forecaster sorts winners from losers. Two forecasters with the "
        "same Brier can have very different REL/RES profiles."
    )
    out_md.write_text("\n".join(lines))
    print(f"wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
