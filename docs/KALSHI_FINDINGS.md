# Kalshi paper — findings we use in the agent

**Source:** PDF on Rob's desktop, downloaded 2026-05-15. _When the
dispatch agent processes this branch, replace this note with the
paper's full title + authors + arXiv link + page-anchored citations._

## Headline finding — favorite-longshot bias is real on Kalshi

Prediction-market prices are **informative but not unbiased**. Across the
Kalshi contract universe the paper documents:

- **Buyers of contracts below $0.10 lose more than 60% on average.**
  This is the single most important number for our agent. LLMs love
  vivid low-probability narratives and will happily push us upward on
  these contracts. The market data says doing so is a money-loser
  unless the evidence is overwhelming.
- **Contracts above $0.50 show small positive returns.** Strong
  favorites tend to be slightly *under*-priced. LLM safety-tuning
  pushes models toward conservative middle-of-the-road probabilities,
  which would systematically shrink us away from these correct
  favorites. We have to actively resist that.
- **Makers outperform Takers.** Both groups show the longshot
  pattern, but the maker side captures more of the available
  edge — the spread eats taker returns. For our paper-trading
  benchmark this maps to: **don't pay the spread unless the edge
  is real**.
- **Modest probability overweighting + disagreement** is the
  paper's offered mechanism. Bettors put too much weight on
  low-probability outcomes, and they disagree among themselves.
  Both forces persist in LLMs (vivid-narrative overweighting +
  ensemble disagreement on low-data outcomes).

## Concrete rules we apply

### 1. Longshot guard (`forecasting.market_blend.kalshi_longshot_guard`)

If `p_market < 0.10`, an LLM is **not** allowed to push our
`p_model` more than 0.05 above the market price unless **both**:

- `source_quality > 0.7` (excellent sources), AND
- `model_agreement > 0.7` (the ensemble agrees)

Otherwise we cap the upward move at `+0.05` from `p_market`.

This is the single highest-impact rule we add. It directly addresses
the >60% loss finding.

### 2. Favorites no-shrink (`forecasting.market_blend.favorites_no_shrink`)

If `p_market > 0.85` and the LLM is **lower** than the market, we
blend conservatively (70% market / 30% model) instead of shrinking
toward 0.50. The market is usually right on near-certain events.

### 3. Longshot proximity → tighter edge gate (`forecasting.market_blend.longshot_proximity` wired into `risk.required_edge`)

For each candidate trade:

```
required_edge += 0.05 * longshot_proximity(p_market)
```

where `longshot_proximity` ramps from 0 (at p_market ≥ 0.10) to 1.0
(at p_market = 0). Equivalent to "you need a fatter edge to justify
trading a long-shot."

### 4. Price-bucket telemetry (`evaluation.returns.pnl_by_price_bucket`)

Every trade logs its `p_market_at_fill`. The post-event report
aggregates PnL by 10 buckets (0.00–0.10, 0.10–0.20, ..., 0.90–1.00).
If we lose money in the 0.00–0.10 bucket after applying the rules
above, the rules need to be tightened.

## What the rules deliberately do NOT do

- We don't refuse to trade longshots entirely. The market sometimes
  *is* wrong; the guards just require excellent evidence to act.
- We don't auto-favorite the favorite. If the LLM strongly disagrees
  with a high-priced market AND the evidence is excellent, the
  credibility-weighted blend can still produce a `p_final` below
  `p_market`. The `favorites_no_shrink` rule only kicks in when
  the LLM is conservative for no clear reason.
- We don't try to replicate maker-side fills. The benchmark uses
  paper-trading with deterministic fills. The maker/taker lesson
  becomes "treat the spread as a real cost in `executable_edge`."

## TODO once the dispatch agent has read the actual PDF

- [ ] Replace the prose above with verbatim page-anchored quotes for
      each of the four headline findings.
- [ ] Add the paper's bibtex entry.
- [ ] Calibrate the exact threshold values against the paper's
      reported bucket-by-bucket return rates (currently using 0.10,
      0.85, 0.7 as conservative defaults).
- [ ] Add a paragraph on Maker-vs-Taker numbers if relevant for our
      `executable_edge` formula.
