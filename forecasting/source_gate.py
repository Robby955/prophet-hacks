"""Source gate — decides WHEN retrieval is worth it.

Retrieval is not "more context." It is a measurement process that
can clarify OR confound, depending on source quality. The Prophet
Arena Bitcoin case study showed bad crypto sources actively worsen
forecasts. We don't retrieve indiscriminately.

The gate produces a `RetrievalDecision` with reasons, so every skip
is auditable in the trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RetrievalDecision:
    should_retrieve: bool
    reasons: tuple
    source_cap: int


# Domains where retrieval almost always helps because primary sources
# exist and update quickly:
HIGH_SIGNAL_DOMAINS = frozenset(
    {"sports", "weather", "elections", "finance", "tech"}
)

# Domains where retrieval often adds noise or stale narratives:
LOW_SIGNAL_DOMAINS = frozenset({"geopolitics", "crypto", "other"})


def should_retrieve(
    *,
    domain: str,
    hours_to_resolution: float,
    market_implied_p_yes: float,
    model_disagreement: float = 0.0,
    cost_budget_remaining_usd: float = 100.0,
) -> RetrievalDecision:
    """Decide whether to spend budget on retrieval for this market.

    Defaults to YES for high-signal domains with adequate horizon and
    cost budget; defaults to NO for low-signal domains or near-resolution
    markets where the market itself is sharper than any source.

    Encoded conditions match the v6 playbook Section 18 "Retrieval and
    source-quality scoring" guidance.
    """
    reasons = []

    if cost_budget_remaining_usd <= 0:
        return RetrievalDecision(False, ("no_budget",), 0)

    # Near-resolution: market is sharpest; retrieval rarely helps and
    # often adds latency
    if hours_to_resolution < 1.0:
        return RetrievalDecision(False, ("near_resolution",), 0)

    if domain in LOW_SIGNAL_DOMAINS and model_disagreement < 0.10:
        reasons.append("low_signal_domain_no_disagreement")
        return RetrievalDecision(False, tuple(reasons), 0)

    # High-signal domain or markets where the model and market disagree
    # → retrieval may help disambiguate
    if domain in HIGH_SIGNAL_DOMAINS:
        reasons.append("high_signal_domain")
        cap = 3 if hours_to_resolution > 24 else 2
        return RetrievalDecision(True, tuple(reasons), cap)

    # Default: retrieve only if there's meaningful model/market gap
    if model_disagreement > 0.10:
        reasons.append("meaningful_disagreement")
        return RetrievalDecision(True, tuple(reasons), 2)

    return RetrievalDecision(False, ("default_skip",), 0)


def retrieval_cost_estimate(source_cap: int, hours_to_resolution: float) -> float:
    """Rough $ cost estimate for the retrieval phase.

    Used by the gate to short-circuit when the budget can't afford it.
    Per playbook the cheap-model-driven source classification is in
    Phase 1 ($), and synthesis of evidence into the prompt is the
    main cost driver.
    """
    if source_cap <= 0:
        return 0.0
    # Rough: $0.02 per retrieved + classified source on the cheap path
    base = 0.02 * source_cap
    # Long-horizon markets benefit from a beat of context but cost more
    if hours_to_resolution > 24 * 7:
        base *= 1.5
    return base
