"""SAE-inspired shrinkage in logit space — Rob's first shippable formula.

This module implements the explicit additive-effects form from the
v6 playbook (Section 16 "SAE implementation sketch"):

    z_final  = logit(p_market)
    z_final += w_model * (logit(p_model) - logit(p_market))
    z_final += alpha_domain[domain]            * reliability_domain
    z_final += alpha_horizon[horizon_bucket]   * reliability_horizon
    z_final += alpha_price[price_bucket]       * reliability_price
    z_final -= beta_disagree * model_disagreement
    p_final  = sigmoid(z_final)

It is intentionally NOT full hierarchical Bayes. It is disciplined
empirical shrinkage where each random effect carries its own
reliability multiplier `n_area / (n_area + k)`, exactly the SAE
shrinkage factor. Effects shrink toward 0 (no adjustment) when the
relevant cell is sparse and toward their own estimate when the cell
is dense.

This composes with `borrowed_strength.borrowed_strength_estimate` but
can also be used standalone. Live use should keep alphas frozen from
the offline-eval calibration; only enable live updates when
organizer rules permit it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

from .market_blend import clamp, logit, sigmoid


# -- Horizon and price buckets --------------------------------------------


HORIZON_BUCKETS = ("near", "medium", "long")  # < 24h, < 7d, >= 7d
PRICE_BUCKETS = (
    "p_00_10", "p_10_20", "p_20_30", "p_30_40", "p_40_50",
    "p_50_60", "p_60_70", "p_70_80", "p_80_90", "p_90_99",
)


def horizon_bucket(hours_to_resolution: float) -> str:
    if hours_to_resolution < 24:
        return "near"
    if hours_to_resolution < 24 * 7:
        return "medium"
    return "long"


def price_bucket(p_market: float) -> str:
    if p_market < 0.10:
        return "p_00_10"
    if p_market < 0.20:
        return "p_10_20"
    if p_market < 0.30:
        return "p_20_30"
    if p_market < 0.40:
        return "p_30_40"
    if p_market < 0.50:
        return "p_40_50"
    if p_market < 0.60:
        return "p_50_60"
    if p_market < 0.70:
        return "p_60_70"
    if p_market < 0.80:
        return "p_70_80"
    if p_market < 0.90:
        return "p_80_90"
    return "p_90_99"


# -- Cell state -----------------------------------------------------------


@dataclass
class CellRandomEffect:
    """One stratum's running estimate + sample count.

    alpha: the additive correction in logit space (centered at 0 by default).
    n:     resolved outcomes that contributed to this cell.
    """

    alpha: float = 0.0
    n: int = 0

    def reliability(self, shrink_k: float = 10.0) -> float:
        return self.n / (self.n + shrink_k) if (self.n + shrink_k) > 0 else 0.0


@dataclass
class SAECalibrator:
    """Holds three sets of random effects (domain / horizon / price)
    plus a global model weight `w_model` and a disagreement penalty
    `beta_disagree`.

    All alphas default to 0 so an unconfigured calibrator is
    equivalent to a pure credibility-weighted market blend; the
    calibration step learns the alphas offline.

    `shrink_k` is the SAE shrinkage parameter. Larger -> stronger
    pull toward 0 (the global pooled estimate). Default 10 matches
    the conservative posture in the v6 playbook.
    """

    w_model: float = 0.50
    beta_disagree: float = 0.30
    shrink_k: float = 10.0
    domain_effects: Dict[str, CellRandomEffect] = field(default_factory=dict)
    horizon_effects: Dict[str, CellRandomEffect] = field(default_factory=dict)
    price_effects: Dict[str, CellRandomEffect] = field(default_factory=dict)

    def _cell(self, table: Dict[str, CellRandomEffect], key: str) -> CellRandomEffect:
        if key not in table:
            table[key] = CellRandomEffect()
        return table[key]

    def predict(
        self,
        *,
        p_market: float,
        p_model: float,
        domain: str,
        hours_to_resolution: float,
        model_disagreement: float = 0.0,
    ) -> float:
        """Apply the SAE-shrinkage composite estimator."""
        z = logit(p_market)
        # Model deviation (always centered on the market prior)
        z += self.w_model * (logit(p_model) - logit(p_market))

        # Three additive random effects, each scaled by its reliability
        dom = self._cell(self.domain_effects, domain)
        horz = self._cell(self.horizon_effects, horizon_bucket(hours_to_resolution))
        price = self._cell(self.price_effects, price_bucket(p_market))

        # Three additive random effects share a single observation, so we
        # average their contributions rather than summing them — otherwise
        # one resolved outcome would move three independent cells and
        # triple-count its own evidence in the prediction.
        z += (
            dom.alpha * dom.reliability(self.shrink_k)
            + horz.alpha * horz.reliability(self.shrink_k)
            + price.alpha * price.reliability(self.shrink_k)
        ) / 3.0

        # Disagreement is a penalty toward 0 (i.e. toward p_market)
        z -= self.beta_disagree * model_disagreement

        return clamp(sigmoid(z))

    def update(
        self,
        *,
        p_market: float,
        p_model: float,
        p_final: float,
        outcome: int,
        domain: str,
        hours_to_resolution: float,
    ) -> None:
        """Online empirical-Bayes update after one outcome resolves.

        Updates each stratum's alpha toward the observed residual
        `logit(outcome) - z_predicted_without_this_cell`. Uses
        decreasing-step-size update (1/n) which is the empirical-Bayes
        MLE for the random-effect mean under a Gaussian prior.

        For binary outcomes we use the log-loss gradient on the
        observed probability, clipped to [0.01, 0.99].
        """
        # We update by attributing the (logit(outcome) - logit(p_final))
        # residual proportionally to each cell's contribution share.
        z_predicted = logit(p_final)
        z_observed = logit(0.99 if outcome == 1 else 0.01)
        residual = z_observed - z_predicted

        # Equal attribution across the three strata for now. A future
        # version could weight by each cell's current reliability.
        share = residual / 3.0

        for key_fn, table in (
            (lambda: domain, self.domain_effects),
            (lambda: horizon_bucket(hours_to_resolution), self.horizon_effects),
            (lambda: price_bucket(p_market), self.price_effects),
        ):
            key = key_fn()
            cell = self._cell(table, key)
            cell.n += 1
            # 1/n averaging
            cell.alpha = cell.alpha + (share - cell.alpha) / cell.n

    def snapshot(self) -> dict:
        # alpha is kept at full precision so snapshot -> from_frozen is a
        # lossless round-trip; reliability is recomputed by the consumer
        # from (n, shrink_k) so we don't persist it.
        return {
            "w_model": self.w_model,
            "beta_disagree": self.beta_disagree,
            "shrink_k": self.shrink_k,
            "domain": {
                k: {"alpha": v.alpha, "n": v.n}
                for k, v in self.domain_effects.items()
            },
            "horizon": {
                k: {"alpha": v.alpha, "n": v.n}
                for k, v in self.horizon_effects.items()
            },
            "price": {
                k: {"alpha": v.alpha, "n": v.n}
                for k, v in self.price_effects.items()
            },
        }

    @classmethod
    def from_frozen(cls, snapshot: dict) -> "SAECalibrator":
        """Reconstruct from a serialized snapshot. Used to load a
        calibrator pretrained offline before live runs."""
        calib = cls(
            w_model=snapshot.get("w_model", 0.50),
            beta_disagree=snapshot.get("beta_disagree", 0.30),
            shrink_k=snapshot.get("shrink_k", 10.0),
        )
        for cell_key, table in (
            ("domain", calib.domain_effects),
            ("horizon", calib.horizon_effects),
            ("price", calib.price_effects),
        ):
            data = snapshot.get(cell_key, {})
            for k, v in data.items():
                table[k] = CellRandomEffect(alpha=v.get("alpha", 0.0), n=v.get("n", 0))
        return calib
