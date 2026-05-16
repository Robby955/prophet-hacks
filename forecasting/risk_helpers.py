"""Risk-gate helpers that compose with risk.py.

Kept separate from risk.py so the locked constants there stay
visually unchanged. The dispatch agent (or human reviewer) wires
`required_edge_with_kalshi_adjustment` into the call site in
forecaster.stage_risk_gate.
"""

from __future__ import annotations

from .market_blend import longshot_proximity

import sys
from pathlib import Path

# Allow `from risk import EDGE_THRESHOLD` even when imported as a
# sibling of the top-level `risk` module.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from risk import EDGE_THRESHOLD  # noqa: E402


def required_edge_with_kalshi_adjustment(
    *,
    p_market: float,
    model_disagreement: float = 0.0,
    source_staleness: float = 0.0,
    spread: float = 0.0,
    base_edge: float = EDGE_THRESHOLD,
) -> float:
    """Margin-of-safety edge gate per the v2.1 / v3 strategy.

    base_edge: hard floor from risk.EDGE_THRESHOLD (locked at 0.08).
    Increased by:
      + 0.50 * model_disagreement
      + 0.25 * source_staleness
      + 0.50 * spread
      + 0.05 * longshot_proximity(p_market)   <-- Kalshi addition

    The Kalshi term means longshots (p_market < 0.10) need a fatter
    edge before we trade. Empirically justified by the >60% loss
    finding on sub-$0.10 contracts.
    """
    uncertainty_load = (
        0.50 * model_disagreement
        + 0.25 * source_staleness
        + 0.50 * spread
        + 0.05 * longshot_proximity(p_market)
    )
    return max(base_edge, base_edge + uncertainty_load)
