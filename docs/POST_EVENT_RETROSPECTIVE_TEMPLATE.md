# Post-event retrospective

Fill in within 7 days of the eval window closing. Be specific. Real
numbers over adjectives. Three things per "worked / did not work /
would change" section, no padding.

---

## Final Brier score

- **Agent Brier:** `<fill in>`
- **Random 0.25 baseline:** 0.2500 (constant; `(0.5 - 0.5)^2`)
- **Market-implied baseline:** `<fill in>` (computed by replaying ticks with `p_yes = market mid`)
- **Delta vs random:** `<fill in>`
- **Delta vs market:** `<fill in>` (negative = beat the market)
- **Bucket-level calibration:** see `reports/<slug>/calibration_plot.png`

## Trade-level performance

- **Total notional traded:** `$<fill in>`
- **Total trades submitted:** `<fill in>`
- **Trade win rate:** `<fill in>` (resolved markets where the trade direction was correct)
- **Average edge captured vs forecasted:** `<fill in>` (capture rate)
- **Maximum drawdown:** `$<fill in>` (from peak bankroll)
- **Final bankroll:** `$<fill in>` (starting: $10,000)
- **Sharpe-equivalent on per-tick PnL:** `<fill in>` (mean / stddev of per-tick PnL)

## Per-variant comparison

Filled in only if multiple variants ran. Otherwise drop this section.

| Variant | Brier | Final bankroll | Trades | Notes |
| --- | --- | --- | --- | --- |
| baseline-market-price | | | | |
| model-forecast-no-retrieval | | | | |
| model-forecast-retrieval | | | | |
| calibrated-ensemble | | | | |

## What worked

Three specific decisions or modules that paid off. Cite commit SHAs.

1. `<fill in>`
2. `<fill in>`
3. `<fill in>`

## What did not work

Three specific decisions or modules that hurt. Cite commit SHAs.

1. `<fill in>`
2. `<fill in>`
3. `<fill in>`

## What I would do differently

Three concrete improvements for the next sprint. Each one specific
enough to be a PR description.

1. `<fill in>`
2. `<fill in>`
3. `<fill in>`

## Methodology highlights

Bullet list of the methodology choices worth showcasing in the portfolio
writeup. Examples:

- Calibration discipline: bucketed probabilities snap to `[0.10, 0.20, ..., 0.90]`.
- Skip-by-default: any uncertainty falls through to a SKIP record.
- Edge threshold 0.08 as a constant in `risk.py`, not a yaml-only knob.
- Idempotency keys on every `TradeIntentRequest` derived from `sha256(tick_id|market_id|side|size)`.
- Multi-model agreement gate (if shipped): triage and forecast must agree on direction.
- Append-only JSONL traces, one record per decision, written before the next decision starts.

## Code organization summary

Single paragraph describing how the four-module split (`agent.py`,
`forecaster.py`, `market_filter.py`, `risk.py`) actually held up under
real conditions. Did the seams hold? Were there modules that grew bigger
than they should have? Was there a missing module that should exist?

## Cost report

- **Anthropic API spend:** $`<fill in>`
- **OpenAI API spend:** $`<fill in>`
- **RunPod spend (if any):** $`<fill in>`
- **Other infra:** $`<fill in>`
- **Total:** $`<fill in>`

## Reproducibility receipt

One-command run that recreates the headline result on a fresh clone.

```bash
git clone <repo>
cd prophet-hacks
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python agent.py --slug <slug-used> --variant <variant-used>
python scripts/build_results_report.py --slug <slug-used>
```
