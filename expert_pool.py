"""Hedge / exponentially-weighted-experts pool for combining forecast variants.

References:
- Bates & Granger (1969) "The Combination of Forecasts", J. Operational Research
- Cesa-Bianchi & Lugosi (2006) *Prediction, Learning, and Games*, Cambridge
- Littlestone & Warmuth (1994) "The Weighted Majority Algorithm"

The Hedge algorithm (a.k.a. exponentially-weighted average forecaster) is a
proper online-learning combiner: each expert i carries a weight w_i, and
after each round the weight is updated by ``w_i *= exp(-eta * L_i)`` where
``L_i`` is expert i's loss on the round and ``eta`` is the learning rate.
The pool's forecast is the weight-normalized average across experts.

For Prophet Arena forecasting:
- Each expert is one of the predict_* variants in forecast_track.
- A "round" is one resolved event; the loss is per-outcome Brier
  ``sum_o (p_o - 1[o==winner])^2``.
- The pool's forecast for a new event is the per-outcome
  weight-normalized average of each variant's `probabilities` payload.

Provides ``ExpertPool`` with three methods:

    pool = ExpertPool(experts={"single_llm": predict_single_llm, ...})
    rec  = pool.predict(event)            # returns {"probabilities": [...]}
    pool.observe(event, winner)            # update weights from the resolution
    pool.save(path) / ExpertPool.load(path)

Cross-link from the TheoremPath ForecastPath topic
``forecast-combinations-and-ensembles.mdx``.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Sequence

log = logging.getLogger("prophet-hacks.expert_pool")

_PredictFn = Callable[[dict], dict]


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        n = len(weights)
        return {k: 1.0 / n for k in weights} if n else {}
    return {k: v / total for k, v in weights.items()}


def per_event_brier(probs_by_outcome: dict[str, float], winner: str) -> float:
    """sum_o (p_o - 1[o==winner])^2 across the outcomes the expert returned.

    Missing outcomes are treated as 0.0 in the expert's distribution, which
    penalizes experts that drop the winner from their support.
    """
    s = 0.0
    seen_winner = False
    for o, p in probs_by_outcome.items():
        y = 1.0 if o == winner else 0.0
        if y == 1.0:
            seen_winner = True
        s += (float(p) - y) ** 2
    if not seen_winner:
        s += 1.0  # expert didn't include the winner -> add the (0-1)^2 = 1 term
    return s


class ExpertPool:
    """Hedge-style weighted pool over a fixed set of predict_* experts."""

    def __init__(
        self,
        experts: dict[str, _PredictFn],
        weights: dict[str, float] | None = None,
        eta: float | None = None,
    ):
        if not experts:
            raise ValueError("ExpertPool needs at least one expert")
        self.experts = dict(experts)
        if weights is None:
            n = len(experts)
            weights = {k: 1.0 / n for k in experts}
        else:
            missing = set(experts) - set(weights)
            if missing:
                raise ValueError(f"weights missing for experts: {missing}")
        self.weights: dict[str, float] = _normalize(weights)
        # Hedge learning rate. Cesa-Bianchi & Lugosi recommend
        # eta = sqrt(8 ln N / T) for known horizon T; we don't know T at
        # hackathon time, so default to a moderate constant 0.5 which
        # corresponds to "halve the weight on a loss of 1.0".
        self.eta: float = eta if eta is not None else 0.5
        self.rounds: int = 0

    # -- prediction --------------------------------------------------------

    def predict(self, event: dict) -> dict[str, Any]:
        """Weighted-average per-outcome forecast across experts.

        Calls each expert in turn (synchronously here -- callers may wrap in
        ThreadPoolExecutor when latency matters). Each expert may return
        either a ``probabilities`` list or just a binary ``p_yes``; we
        normalize to a per-outcome dict before averaging.
        """
        outcomes: list[str] = list(event.get("outcomes") or [])
        if not outcomes:
            return {"p_yes": 0.5, "rationale": "no outcomes", "probabilities": []}

        expert_dists: dict[str, dict[str, float]] = {}
        rationales: list[str] = []
        for name, fn in self.experts.items():
            try:
                r = fn(event)
            except Exception as e:
                log.warning("expert %s failed for %s: %s", name,
                            event.get("market_ticker", "?"), e)
                continue
            dist = _dist_from_expert_result(r, outcomes)
            expert_dists[name] = dist
            rat = str(r.get("rationale", ""))[:80]
            if rat:
                rationales.append(f"{name}: {rat}")

        if not expert_dists:
            return {"p_yes": 1.0 / len(outcomes), "rationale": "all experts failed",
                    "probabilities": [
                        {"market": o, "probability": 1.0 / len(outcomes)}
                        for o in outcomes
                    ]}

        # Weighted average across experts. Drop experts whose weight is zero.
        agg: dict[str, float] = {o: 0.0 for o in outcomes}
        live_total = 0.0
        for name, dist in expert_dists.items():
            w = self.weights.get(name, 0.0)
            if w <= 0:
                continue
            live_total += w
            for o in outcomes:
                agg[o] += w * dist.get(o, 0.0)
        if live_total > 0:
            for o in outcomes:
                agg[o] /= live_total

        probs = [{"market": o, "probability": agg[o]} for o in outcomes]
        return {
            "p_yes": agg.get(outcomes[0], 1.0 / len(outcomes)),
            "rationale": "pool(" + ",".join(rationales[:3])[:240] + ")",
            "probabilities": probs,
            "expert_distributions": expert_dists,  # for downstream loss updates
        }

    # -- weight updates ----------------------------------------------------

    def observe(
        self,
        event: dict,
        winner: str,
        expert_distributions: dict[str, dict[str, float]] | None = None,
    ) -> None:
        """Apply Hedge update from one resolved event.

        ``winner`` is the resolved outcome label (a string in event.outcomes).
        ``expert_distributions`` is the per-expert dict as returned by
        ``predict()``; if omitted, we re-run each expert (extra cost). Best
        practice: capture from `predict()` immediately, then call
        ``observe()`` once the event resolves later.
        """
        outcomes = list(event.get("outcomes") or [])
        if winner not in outcomes:
            log.warning(
                "observe: winner %r not in event.outcomes %r", winner, outcomes,
            )
            return
        if expert_distributions is None:
            expert_distributions = {}
            for name, fn in self.experts.items():
                try:
                    r = fn(event)
                except Exception as e:
                    log.warning("expert %s failed on observe re-call: %s", name, e)
                    continue
                expert_distributions[name] = _dist_from_expert_result(r, outcomes)

        # Per-expert loss (per-outcome Brier across the event's outcomes).
        for name in self.experts:
            dist = expert_distributions.get(name)
            if dist is None:
                continue  # expert failed; leave weight unchanged
            loss = per_event_brier(dist, winner)
            self.weights[name] = self.weights[name] * math.exp(-self.eta * loss)

        self.weights = _normalize(self.weights)
        self.rounds += 1

    # -- persistence -------------------------------------------------------

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({
            "weights": self.weights,
            "eta": self.eta,
            "rounds": self.rounds,
        }, indent=2))

    @classmethod
    def load(
        cls,
        path: str | Path,
        experts: dict[str, _PredictFn],
    ) -> "ExpertPool":
        data = json.loads(Path(path).read_text())
        pool = cls(
            experts=experts,
            weights=data.get("weights"),
            eta=data.get("eta", 0.5),
        )
        pool.rounds = int(data.get("rounds", 0))
        return pool


def _dist_from_expert_result(
    result: dict, outcomes: Sequence[str],
) -> dict[str, float]:
    """Normalize an expert's `result` to a {outcome: probability} dict.

    Handles both the new `probabilities` shape and the legacy single-`p_yes`
    shape (which is interpreted as outcomes[0] = p_yes, the rest evenly
    splitting 1 - p_yes).
    """
    raw = result.get("probabilities")
    if isinstance(raw, list) and raw and all(
        isinstance(p, dict) and "market" in p and "probability" in p for p in raw
    ):
        out = {str(p["market"]): float(p["probability"]) for p in raw}
        # Ensure every outcome is present
        for o in outcomes:
            out.setdefault(o, 0.0)
        return out
    # Legacy: distribute p_yes
    p_yes = float(result.get("p_yes", 1.0 / max(1, len(outcomes))))
    p_yes = max(0.01, min(0.99, p_yes))
    n = len(outcomes)
    if n == 0:
        return {}
    if n == 1:
        return {outcomes[0]: p_yes}
    rest = max(0.0, (1.0 - p_yes) / (n - 1))
    return {o: (p_yes if i == 0 else rest) for i, o in enumerate(outcomes)}


__all__ = [
    "ExpertPool",
    "per_event_brier",
    "_dist_from_expert_result",
]
