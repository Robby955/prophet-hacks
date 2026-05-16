"""Bidirectional probability elicitation.

Prophet Arena paper ablation finding: asking the model for p_yes AND
p_no separately, then combining as 0.5 * (p_yes + (1 - p_no)),
improved calibration on 4 of 5 evaluated models. Self-consistency
rollouts were *worse* for accuracy and more expensive.

We only apply this to selected markets (near threshold, high-value,
or sources-conflict) because it doubles the LLM call cost per market.
"""

from __future__ import annotations

from .market_blend import clamp


def combine_bidirectional(p_yes: float, p_no: float) -> float:
    """Combine independently-elicited p_yes and p_no into a single p.

    p_yes and p_no should sum to ~1.0 if the model is internally consistent,
    but in practice they don't quite. The combination treats them as two
    noisy estimates of the YES probability:

        p_final = 0.5 * (p_yes + (1 - p_no))

    Returns clamped result.
    """
    p_yes = clamp(p_yes)
    p_no = clamp(p_no)
    combined = 0.5 * (p_yes + (1.0 - p_no))
    return clamp(combined)


def bidirectional_consistency(p_yes: float, p_no: float) -> float:
    """A consistency check: how close p_yes + p_no is to 1.0.

    Returns 1.0 when perfectly consistent, 0.0 when maximally inconsistent.
    Useful as an additional confidence signal — strongly inconsistent
    bidirectional elicitations should reduce credibility, not be averaged
    blindly.
    """
    drift = abs((p_yes + p_no) - 1.0)
    # Drift of 0 -> 1.0; drift of >= 0.4 -> 0.0
    return max(0.0, 1.0 - (drift / 0.4))


def should_bidirectional(
    *,
    edge: float,
    edge_threshold: float = 0.08,
    market_notional: float = 0.0,
    high_notional_floor: float = 500.0,
    sources_conflict: float = 0.0,
) -> bool:
    """Gate function: should we pay 2x to bidirectionally elicit on this market?

    YES if:
      - edge is within 50% of the threshold (near the decision boundary), OR
      - market notional is high enough to justify extra LLM cost, OR
      - source disagreement is high (> 0.3) — bidirectional helps reveal
        when the model itself is uncertain about the direction.
    """
    near_threshold = abs(edge - edge_threshold) <= 0.5 * edge_threshold
    high_notional = market_notional >= high_notional_floor
    sources_conflicted = sources_conflict > 0.3
    return near_threshold or high_notional or sources_conflicted
