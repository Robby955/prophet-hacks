"""Domain-level partial pooling — the SAE random-effects layer.

Maintains a running Brier-loss tracker per (model, domain) cell and
returns a domain-specific calibration offset. The pool is updated
online as outcomes resolve.

Borrowing intuition: when this domain's track record is short, the
domain's offset shrinks toward the model's marginal (across all
domains) offset. As more outcomes accumulate, the offset relies more
on the domain's own data. This is the empirical-Bayes / Fay-Herriot
analogue from Small Area Estimation: thin areas borrow from the
larger pool, dense areas use their own data.

References:
- Rao & Molina, Small Area Estimation (2nd ed), Wiley, 2015.
- Fay & Herriot, "Estimates of income for small places: an
  application of James-Stein procedures to census data,"
  JASA 1979.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


DOMAINS = (
    "sports",
    "finance",
    "crypto",
    "weather",
    "elections",
    "science",
    "tech",
    "geopolitics",
    "health",
    "other",
)


@dataclass
class CellState:
    """Running stats for one (model, domain) cell."""

    n: int = 0
    mean_brier: float = 0.25  # random-baseline prior

    def update(self, brier: float) -> None:
        self.n += 1
        self.mean_brier += (brier - self.mean_brier) / self.n


@dataclass
class DomainPool:
    """Hierarchical (model, domain) reliability tracker.

    Each (model, domain) cell has its own running Brier. The pooled
    estimate for any cell shrinks toward the model's marginal mean
    Brier (across all domains) when n is small.

    Shrinkage weight uses a Fay-Herriot-style formula:
        gamma_md = n_md / (n_md + lambda)
    where `lambda` controls how aggressively thin cells borrow from
    the marginal. Default lambda = 10 means a cell with 10 outcomes
    weights its own data 50/50 with the marginal.
    """

    lambda_prior: float = 10.0
    cells: Dict[Tuple[str, str], CellState] = field(default_factory=dict)
    marginals: Dict[str, CellState] = field(default_factory=dict)  # per model

    def update(self, model: str, domain: str, brier: float) -> None:
        if domain not in DOMAINS:
            domain = "other"
        cell_key = (model, domain)
        if cell_key not in self.cells:
            self.cells[cell_key] = CellState()
        if model not in self.marginals:
            self.marginals[model] = CellState()
        self.cells[cell_key].update(brier)
        self.marginals[model].update(brier)

    def pooled_brier(self, model: str, domain: str) -> float:
        """Empirical-Bayes shrinkage estimate of this (model, domain)
        cell's expected Brier.
        """
        if domain not in DOMAINS:
            domain = "other"
        cell = self.cells.get((model, domain))
        marginal = self.marginals.get(model)
        if cell is None and marginal is None:
            return 0.25
        if cell is None:
            return marginal.mean_brier
        if marginal is None:
            return cell.mean_brier
        gamma = cell.n / (cell.n + self.lambda_prior)
        return gamma * cell.mean_brier + (1.0 - gamma) * marginal.mean_brier

    def domain_offset(self, model: str, domain: str) -> float:
        """Calibration offset for this (model, domain) vs the model's
        overall mean. Positive = domain is harder than this model's
        average; negative = domain is easier.

        Use to shrink p_model toward p_market harder on bad domains
        and trust p_model more on good domains:

            adjusted_credibility = base_credibility *
                exp(-eta * domain_offset(model, domain))
        """
        pooled = self.pooled_brier(model, domain)
        marginal = self.marginals.get(model)
        if marginal is None:
            return 0.0
        return pooled - marginal.mean_brier

    def snapshot(self) -> Dict[str, Dict[str, dict]]:
        """Serializable view for logging / status.md."""
        out: Dict[str, Dict[str, dict]] = {}
        for (model, domain), cell in self.cells.items():
            out.setdefault(model, {})[domain] = {
                "n": cell.n,
                "mean_brier": round(cell.mean_brier, 4),
                "pooled_brier": round(self.pooled_brier(model, domain), 4),
                "domain_offset": round(self.domain_offset(model, domain), 4),
            }
        for model, marg in self.marginals.items():
            out.setdefault(model, {})["__marginal__"] = {
                "n": marg.n,
                "mean_brier": round(marg.mean_brier, 4),
            }
        return out
