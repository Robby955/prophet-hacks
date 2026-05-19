# Handoff — 2026-05-19 (afternoon)

Single-page state for picking up cold. Two agents (Claude + Codex) working in
parallel; the **Work split** section assigns file-level lanes to avoid merge
collisions. Non-negotiables at the bottom.

## Live state

- **Agent**: healthy. `agent.forecastingpath.com/healthz` → ok, commit
  `a1f899b0` (top-K classifier + narrowed binary shortcut), Brave configured,
  variant `multi_outcome_retrieval`.
- **Real PA traffic: still none.** All 1,927 `/predict` hits are synthetic —
  PA's four fixtures (T1/T2/T7/T8) on a ~30s replay + unlabeled heartbeats.
  First real market is still the trigger for payload-shape / latency / scoring
  validation. **Watch the first payload for a market-price field** (would make
  abstain-to-market trivial).
- **Production is frozen** unless a change clears the promotion gate (below).

## Today's headline finding (logged in DECISIONS.md 2026-05-19)

Search-provider bake-off — model/prompt/guard held constant, only retrieval
swapped (`scripts/ablate_search_provider.py`), n=26 resolved set:

| arm | mean Brier | leakage % |
| --- | --- | --- |
| brave (control, unfiltered) | **0.0377** | 21.3% |
| brave_fresh (cutoff at close − 1d) | **0.1179** | 11.5% |

Paired bootstrap CI: delta −0.080, 95% CI **[−0.136, −0.030]**, Pr(≤0)=1.0000.
Removing hindsight from retrieval makes us **3.1× worse** — independent,
mechanism-level confirmation of the Subset-1200 "3.2× inflated" result. **The
honest out-of-sample estimate is ≈0.118, not 0.038.** Live PA traffic gets the
"fresh" condition for free (events unresolved at query time), so this is a
**backtest-methodology / honesty fix, not a production change.**

### Reframe this unlocks
Abstain-to-market and SAE shrinkage were both rejected for losing to **0.0379**.
SAE shrinkage scored **0.1157** — essentially **tied** with the honest 0.118.
The "abstain is much worse" verdict was an artifact of a hindsight baseline and
should be re-opened.

## New tools added today (untracked — needs commit)

- `scripts/auto_resolve_finance.py` — auto-resolves FIN/CRYPTO shadow events
  from Yahoo (equities) + Coinbase (crypto) via curl; merges into
  `data/shadow_calibration/resolutions.json` without clobbering manual entries.
  `--dry-run` / `--force`. Already wrote SPY/QQQ/GLD (all No) for 2026-05-19.
- `scripts/ablate_search_provider.py` — the bake-off harness. Brave + brave_fresh
  run; Tavily/Exa/Serper activate when their key is in env. Emits standard
  prediction files → feeds `bootstrap_brier_ci.py` directly.

## Open work — proposed lanes (adjust as needed)

**Claude lane** (measurement + doc honesty; touches DECISIONS.md, scripts/, reports/):
1. Re-baseline audience surfaces to honest 0.118 as primary; 0.038 = best-case-with-hindsight.
2. Re-run SAE/abstain vs the `brave_fresh` baseline (~$3) — may flip the negative result.
3. Real multi-prompt sweep via `ablate_prompt.py` with bootstrap CIs.

**Codex lane** (frontend/observatory + infra; touches static/, forecast_agent_server.py, dashboards):
1. Surface the leakage finding on the dashboard: honest-vs-hindsight panel,
   per-provider leakage %. Replace pinned 0.0378 headline.
2. Grow the shadow event set + a cron/loop around `auto_resolve_finance.py` +
   `score_shadow_calibration.py` so the resolved-n climbs automatically.
3. (If Rob drops Tavily/Exa/Serper keys in `~/Desktop/variables.txt`) run the
   provider sweep for the relevance axis.

**Shared / coordinate before touching:** `forecast_track.py` (production path),
`forecast_agent_server.py` (live endpoint), `data/resolved.json`. Ping in
DECISIONS.md before editing these.

## Non-negotiables

1. **Promotion gate**: any production change needs |delta| > 0.01 single-binary
   Brier AND 95% paired-bootstrap CI excluding zero on the offline backtest.
   Measure first, merge second. (2026-05-17 17:30 CT regression is the precedent.)
2. **Shadow loop is research-only** — never promote off small shadow samples.
3. **Mid-window deploys** get a DECISIONS.md entry with SHA + UTC time.
4. **No new keys needed for live**: only PA_SERVER_API_KEY + ANTHROPIC_API_KEY.
   Secrets live in `~/Desktop/variables.txt`; never commit them.
