"""Hierarchical reliability estimation across models and domains.

Sits on top of `domain_pools.DomainPool` and exposes a simple API for
the forecaster: "given that I'm about to call <model> on a <domain>
market, how much should I trust this model relative to the market?"

Returns a multiplier in [0, 1.5] that scales the credibility-weighted
blend's model_weight. Capped to prevent runaway either direction.

The shape:
    multiplier = exp(-eta * (pooled_brier - reference_brier))
where reference_brier is a calibration baseline (default 0.20, roughly
what a competent forecaster achieves on resolved Kalshi markets).

Loaded with a JSON snapshot at startup; persists snapshots on every
resolved-outcome update.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .domain_pools import DomainPool


@dataclass
class ReliabilityTracker:
    """Wraps DomainPool with persistence + a `trust_multiplier` API."""

    pool: DomainPool
    state_path: Optional[Path] = None
    eta: float = 3.0
    reference_brier: float = 0.20
    multiplier_min: float = 0.30
    multiplier_max: float = 1.50

    def load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            with self.state_path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "model" in entry and "domain" in entry and "brier" in entry:
                        self.pool.update(entry["model"], entry["domain"], entry["brier"])
        except OSError:
            return

    def save(self, last_brier: dict) -> None:
        """Append-only event log. `last_brier` is one row like
        {model, domain, brier, market_id, ts}."""
        if self.state_path is None:
            return
        try:
            with self.state_path.open("a") as f:
                f.write(json.dumps(last_brier) + "\n")
        except OSError:
            return

    def record(
        self,
        *,
        model: str,
        domain: str,
        brier: float,
        market_id: str = "",
        ts: str = "",
    ) -> None:
        """Update the running pool and persist the event."""
        self.pool.update(model, domain, brier)
        self.save(
            {"model": model, "domain": domain, "brier": brier, "market_id": market_id, "ts": ts}
        )

    def trust_multiplier(self, model: str, domain: str) -> float:
        """How much to scale this model's credibility on this domain.

        > 1.0 means the (model, domain) cell has been outperforming the
        reference Brier; < 1.0 means it's been worse. Clamped to
        [multiplier_min, multiplier_max].
        """
        pooled = self.pool.pooled_brier(model, domain)
        raw = math.exp(-self.eta * (pooled - self.reference_brier))
        return max(self.multiplier_min, min(self.multiplier_max, raw))


def default_tracker(state_path: Optional[Path] = None) -> ReliabilityTracker:
    """Convenience constructor with sensible defaults."""
    return ReliabilityTracker(
        pool=DomainPool(),
        state_path=state_path,
    )
