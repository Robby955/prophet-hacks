#!/usr/bin/env python3
"""Composite scoring inspired by OpenForecaster's modified-GRPO reward.

Brier alone rewards calibration but underweights directional accuracy
when the market is mispriced. Accuracy alone rewards directional bets
but encourages overconfidence. The OpenForecaster ablation showed the
composite (accuracy + Brier) is meaningfully better than either alone.

We adapt this to RUNTIME scoring (we don't fine-tune anything — same
principle, no GRPO). The composite penalizes confident-and-wrong harder
than uncertain-and-wrong:

    score = brier_loss
          + lambda_overconfident * 1[wrong_direction] * confidence^2
          + lambda_longshot * 1[p_market<0.10 AND lift>0.05]
          + lambda_cost * model_call_cost
          + lambda_fragile_source * fragile_source_count

Lower is better. We use this for:
- Offline variant ranking (replaces "lowest Brier wins" with calibrated
  selection criterion)
- Live trade gate sanity check (a forecast with a bad composite score
  should never trade even if its naive edge clears the threshold)
- Hyperparameter search target
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent


# Default lambda weights. Tune offline; freeze for live.
DEFAULT_LAMBDA_OVERCONFIDENT = 0.50
DEFAULT_LAMBDA_LONGSHOT = 0.20
DEFAULT_LAMBDA_COST = 0.10  # per $1 of model call cost; assumes cost in USD
DEFAULT_LAMBDA_FRAGILE_SOURCE = 0.05  # per fragile source counted


@dataclass(frozen=True)
class CompositeScore:
    market_id: str
    brier: float
    overconfidence_penalty: float
    longshot_penalty: float
    cost_penalty: float
    fragile_source_penalty: float
    total: float


def composite_score(
    *,
    p_final: float,
    p_market: float,
    outcome: Optional[int],
    model_call_cost_usd: float = 0.0,
    fragile_source_count: int = 0,
    p_model_raw: Optional[float] = None,
    lambda_overconfident: float = DEFAULT_LAMBDA_OVERCONFIDENT,
    lambda_longshot: float = DEFAULT_LAMBDA_LONGSHOT,
    lambda_cost: float = DEFAULT_LAMBDA_COST,
    lambda_fragile_source: float = DEFAULT_LAMBDA_FRAGILE_SOURCE,
    market_id: str = "",
) -> CompositeScore:
    """Compute the per-forecast composite score. When `outcome` is None
    (unresolved), the Brier + overconfidence terms are 0 — the score is
    just the operational penalties.
    """
    if outcome is None:
        brier = 0.0
        overconfidence = 0.0
    else:
        brier = (p_final - outcome) ** 2
        confidence = abs(p_final - 0.5)
        wrong_direction = (
            (outcome == 1 and p_final < 0.5)
            or (outcome == 0 and p_final > 0.5)
        )
        overconfidence = (
            lambda_overconfident * (confidence ** 2) if wrong_direction else 0.0
        )

    # Longshot lift penalty
    longshot_pen = 0.0
    if p_market < 0.10 and p_model_raw is not None:
        lift = p_model_raw - p_market
        if lift > 0.05:
            longshot_pen = lambda_longshot

    cost_pen = lambda_cost * max(0.0, model_call_cost_usd)
    fragile_pen = lambda_fragile_source * max(0, fragile_source_count)

    return CompositeScore(
        market_id=market_id,
        brier=brier,
        overconfidence_penalty=overconfidence,
        longshot_penalty=longshot_pen,
        cost_penalty=cost_pen,
        fragile_source_penalty=fragile_pen,
        total=brier + overconfidence + longshot_pen + cost_pen + fragile_pen,
    )


def score_trace(rows: Iterable[dict], **kwargs) -> List[CompositeScore]:
    out = []
    for r in rows:
        outcome = r.get("outcome") if r.get("outcome") in (0, 1) else None
        out.append(
            composite_score(
                p_final=float(r["p_final"]),
                p_market=float(r["p_market"]),
                outcome=outcome,
                model_call_cost_usd=float(r.get("model_call_cost_usd", 0.0)),
                fragile_source_count=int(r.get("fragile_source_count", 0)),
                p_model_raw=(
                    float(r["p_model_raw"]) if r.get("p_model_raw") is not None else None
                ),
                market_id=r.get("market_id", ""),
                **kwargs,
            )
        )
    return out


def load_jsonl(path: Path) -> Iterable[dict]:
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def summarize(scores: Sequence[CompositeScore]) -> dict:
    if not scores:
        return {"n": 0}
    n = len(scores)
    return {
        "n": n,
        "mean_total": sum(s.total for s in scores) / n,
        "mean_brier": sum(s.brier for s in scores) / n,
        "mean_overconfidence_penalty": sum(s.overconfidence_penalty for s in scores) / n,
        "mean_longshot_penalty": sum(s.longshot_penalty for s in scores) / n,
        "mean_cost_penalty": sum(s.cost_penalty for s in scores) / n,
        "mean_fragile_penalty": sum(s.fragile_source_penalty for s in scores) / n,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", type=Path, required=True,
                    help="JSONL trace from a prior run")
    ap.add_argument("--lambda-overconfident", type=float,
                    default=DEFAULT_LAMBDA_OVERCONFIDENT)
    ap.add_argument("--lambda-longshot", type=float,
                    default=DEFAULT_LAMBDA_LONGSHOT)
    ap.add_argument("--lambda-cost", type=float, default=DEFAULT_LAMBDA_COST)
    ap.add_argument("--lambda-fragile-source", type=float,
                    default=DEFAULT_LAMBDA_FRAGILE_SOURCE)
    args = ap.parse_args()

    rows = list(load_jsonl(args.trace))
    scores = score_trace(
        rows,
        lambda_overconfident=args.lambda_overconfident,
        lambda_longshot=args.lambda_longshot,
        lambda_cost=args.lambda_cost,
        lambda_fragile_source=args.lambda_fragile_source,
    )
    summary = summarize(scores)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
