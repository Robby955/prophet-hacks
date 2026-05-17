# Post-event retrospective

Fill this in within 7 days of the evaluation window closing. Use real
numbers, commit SHAs, and trace references. Do not turn weak evidence
into strong claims.

---

## Final outcome

- **Team Brier:** `<fill in>`
- **Market Brier:** `<fill in>`
- **Delta versus market:** `<fill in>` (negative means we beat market)
- **Random 0.5 baseline:** `0.2500`
- **Uniform prior baseline:** `<fill in>`
- **Brier Skill Score versus market:** `<fill in>`
- **Events scored:** `<fill in>`
- **Endpoint completion rate:** `<fill in>` successful calls / attempted calls
- **Live commit or commits:** `<fill in>`
- **Production variant:** `<fill in>`

## Live call audit

Use `/predictions`, Railway logs, and any Prophet Arena result export.

- **Payload shape received:** `<fill in>`
- **Outcome count distribution:** `<fill in>`
- **Prediction schema returned:** `<fill in>`
- **Probability range:** `<fill in min/max>`
- **Fallback count:** `<fill in>`
- **Parse path breakdown:** `<fill in>`
- **Warning count and common warnings:** `<fill in>`
- **Latency p50 / p95 / max:** `<fill in>`
- **Brave retrieval coverage:** `<fill in>` events with non-empty evidence / total
- **Largest single-event Brier loss:** `<fill in>`
- **Best single-event win versus market:** `<fill in>`

## Offline versus live gap

- **Sample-resolved backtest Brier:** `<fill in>`
- **Bootstrap CI used before event:** `<fill in>`
- **Live Brier:** `<fill in>`
- **Did the offline ranking predict live ranking?** `<yes/no/unclear>`
- **Evidence timestamp leakage risk:** `<fill in>`
- **Event mix difference:** `<fill in binary vs multi-outcome counts>`
- **Which offline claims survived live scoring?** `<fill in>`
- **Which offline claims should be downgraded?** `<fill in>`

## What worked

Three specific decisions or modules that paid off. Cite commit SHAs and
the metric or trace that supports each one.

1. `<fill in>`
2. `<fill in>`
3. `<fill in>`

## What did not work

Three specific decisions or modules that hurt or failed to matter. Cite
commit SHAs and the evidence.

1. `<fill in>`
2. `<fill in>`
3. `<fill in>`

## If the score was poor

State the failure mode plainly. Pick the smallest defensible explanation
that matches the data.

- `<fill in: payload/schema mismatch, retrieval weakness, market baseline
  dominance, leakage in offline eval, event-mix mismatch, calibration
  failure, deploy/auth/routing issue, sparse sample, or another measured
  cause>`
- **Trace or script proving it:** `<fill in>`
- **What would have caught it earlier:** `<fill in>`
- **One next experiment:** `<fill in>`

## What I would change next

Three concrete follow-up PRs, each narrow enough to implement.

1. `<fill in>`
2. `<fill in>`
3. `<fill in>`

## Methodology notes worth preserving

Keep only points that remain true after live scoring.

- Forecasts were scored with Brier, not accuracy.
- The endpoint returned the Prophet Arena `probabilities` schema.
- Candidate changes were promoted only after paired comparisons cleared
  the uncertainty bar.
- Negative ablations were documented rather than quietly discarded.
- Per-call traces captured retrieval, model output, parsing, latency, and
  fallback behavior.
- The live dashboard and review pages were auth-gated while the public
  landing page remained safe to share.

## Portfolio artifacts to keep

- `submission/REPORT.md`
- `submission/PROJECT_STORY.md`
- `docs/FINDINGS.md`
- `docs/WORKSHOP_PAPER_DRAFT.md`
- `docs/ADVERSARIAL_REVIEW.md`
- `docs/DECISIONS.md`
- `docs/RUNBOOK.md`
- `docs/HANDOFF.md`
- `docs/QUANT_PORTFOLIO_ARTIFACTS.md`
- `output/playwright/` captures that are safe to publish
- `data/predictions/` backtest outputs
- sanitized `/predictions` live traces, if available

## Cost report

- **Anthropic API spend:** `$<fill in>`
- **OpenAI API spend:** `$<fill in>`
- **Brave Search spend:** `$<fill in>`
- **Railway spend:** `$<fill in>`
- **Other infra:** `$<fill in>`
- **Total:** `$<fill in>`

## Reproducibility receipt

Commands that should recreate the headline analysis on a fresh clone
after actuals are available.

```bash
git clone <repo>
cd prophet-hacks
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/agent/verify.sh
./scripts/post_event_orchestrator.sh --actuals <path-to-actuals>
./scripts/full_check.sh
```

If any command requires a private token, state that clearly and include a
safe public substitute where possible.
