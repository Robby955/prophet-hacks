"""Hedge-style expert pool for online learning across forecasting variants.

This is the RL-lite layer from the v3 strategy. Each forecasting
variant (market_only, gpt55, opus, market_blend, etc.) is an "expert."
After every resolved market, we update each expert's weight based on
its Brier loss, then use the weighted vote on subsequent forecasts.

Deep RL is overkill here. Multiplicative-weight updates (Hedge / EXP3)
are the right shape for a 30-hour competition with sparse outcomes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional


@dataclass
class ExpertState:
    name: str
    weight: float
    mean_brier: float
    n_resolved: int
    initial_weight: float = 1.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "weight": self.weight,
            "mean_brier": self.mean_brier,
            "n_resolved": self.n_resolved,
            "initial_weight": self.initial_weight,
        }


@dataclass
class ExpertPool:
    """Stateful pool of forecasting experts with Hedge-style updates.

    Parameters
    ----------
    eta : float
        Learning rate. Higher -> faster adaptation, more variance.
        Default 0.5 is conservative; matches a half-life of ~1.4 Brier units.
    min_weight : float
        Floor on any expert's weight after normalization. Prevents
        deprecating an expert to zero based on a few bad samples.
    """

    eta: float = 0.5
    min_weight: float = 0.02
    experts: Dict[str, ExpertState] = field(default_factory=dict)

    def register(self, name: str, initial_weight: float = 1.0) -> None:
        if name in self.experts:
            return
        self.experts[name] = ExpertState(
            name=name,
            weight=initial_weight,
            mean_brier=0.25,
            n_resolved=0,
            initial_weight=initial_weight,
        )
        # If no expert has been updated yet, recompute every weight from
        # its declared initial weight before normalizing. This makes
        # registration order irrelevant to the final ratios — register
        # two experts at 0.5 each and they really are 50/50, not
        # 67/33 from a stale intermediate normalization.
        if all(e.n_resolved == 0 for e in self.experts.values()):
            for e in self.experts.values():
                e.weight = e.initial_weight
        self._normalize()

    def get_weights(self) -> Dict[str, float]:
        return {n: e.weight for n, e in self.experts.items()}

    def predict(self, expert_forecasts: Dict[str, float]) -> float:
        """Weighted prediction across registered experts.

        expert_forecasts: {expert_name: p_yes from that expert}
        Returns weighted-mean p_yes.
        """
        total = 0.0
        norm = 0.0
        for name, p in expert_forecasts.items():
            if name not in self.experts:
                continue
            w = self.experts[name].weight
            total += w * p
            norm += w
        if norm <= 0:
            return 0.5
        return total / norm

    def update(self, expert_briers: Dict[str, float]) -> None:
        """Multiplicative update after a market resolves.

        expert_briers: {expert_name: Brier loss observed this round}
            Brier = (p_predicted - outcome) ** 2, in [0, 1].

        weight_i ← weight_i * exp(-eta * brier_i)
        Then renormalize so weights sum to 1.
        """
        for name, brier in expert_briers.items():
            if name not in self.experts:
                continue
            e = self.experts[name]
            # Running mean Brier
            e.n_resolved += 1
            e.mean_brier = e.mean_brier + (brier - e.mean_brier) / e.n_resolved
            # Multiplicative weight update
            e.weight *= math.exp(-self.eta * brier)
        self._normalize()

    def initialize_from_history(
        self, historical_mean_briers: Dict[str, float]
    ) -> None:
        """Set initial weights from offline-eval Brier scores.

        weight_i ∝ exp(-eta * mean_brier_i). Renormalized.
        Useful for warm-starting the pool from the pastcasting harness
        before the live evaluation window opens.
        """
        for name, mb in historical_mean_briers.items():
            if name not in self.experts:
                self.register(name)
            self.experts[name].mean_brier = mb
            self.experts[name].weight = math.exp(-self.eta * mb)
            self.experts[name].n_resolved = 0  # not yet live-resolved
        self._normalize()

    def _normalize(self) -> None:
        if not self.experts:
            return
        total = sum(e.weight for e in self.experts.values())
        if total <= 0:
            n = len(self.experts)
            for e in self.experts.values():
                e.weight = 1.0 / n
            return
        for e in self.experts.values():
            e.weight = e.weight / total
        # Apply minimum-weight floor with proper redistribution: anything
        # below the floor gets set to the floor, and the remaining mass
        # is allocated across the others in proportion to their existing
        # weights so the final sum is still 1.0 and every weight >= floor.
        floor = self.min_weight
        n_experts = len(self.experts)
        if floor * n_experts >= 1.0:
            for e in self.experts.values():
                e.weight = 1.0 / n_experts
            return
        below = [name for name, e in self.experts.items() if e.weight < floor]
        if not below:
            return
        above = [name for name in self.experts if name not in set(below)]
        for name in below:
            self.experts[name].weight = floor
        remaining = 1.0 - floor * len(below)
        total_above = sum(self.experts[name].weight for name in above)
        if total_above <= 0 or not above:
            return
        for name in above:
            self.experts[name].weight = (
                self.experts[name].weight / total_above * remaining
            )
