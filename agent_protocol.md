# Agent protocol — handoff for any coding agent working on this repo

Owner: **Rob Sneiderman** (robbysneiderman@gmail.com — no dot variant).
Project: **Prophet Hacks 2026** forecasting/trading agent.
Read this BEFORE making any change.

## Default system (locked)

```
market prior
+ GPT-5.5 primary forecast
+ optional Claude Opus 4.7 review
+ targeted evidence
+ logit stacking
+ SAE-inspired shrinkage (sae_shrinkage.SAECalibrator)
+ Kalshi longshot guard (market_blend.kalshi_longshot_guard)
+ strict risk gate (risk.py constants, locked)
+ JSONL traces
```

Do not build a raw LLM oracle.
Do not let the model decide trade size.
Code owns edge, risk, sizing, and logging.

## Frontier-model policy

- **GPT-5.5** for primary final forecast.
- **Claude Opus 4.7** for cross-check ONLY when near-threshold, evidence conflicts, sources contradict, or domain is high-stakes (politics / macro / geopolitics / health / science).
- **Cheaper models** (gpt-5.4-mini, claude-haiku-4-5-20251001) for routing, source labels, format validation, obvious skips, and offline ablations.
- Exact model names MUST stay configurable via env vars (`PROPHET_TRIAGE_MODEL`, `PROPHET_STRONG_MODEL`, `PROPHET_OPTIONAL_STRONG_MODEL`). Provider catalogs change.

## Batch vs synchronous

- **Synchronous live API**: live tick forecasts, final trade decisions, source classification on a live market.
- **Batch (OpenAI Batch API, 50% discount, 24h turnaround)**: offline ablations, prompt sweeps, source-label generation, embedding/index construction, hyperparameter search, LLM-judge audits.
- NEVER use Batch for live ticks. NEVER use sync for an offline 500-task ablation.

## Cost caps (locked unless Rob changes)

```yaml
max_batch_forecasts: 500
max_frontier_calls_per_tick: 6
max_opus_escalations_per_tick: 2
max_cost_per_offline_eval_usd: 75
max_cost_per_live_tick_usd: 3
```

## GitHub convergence

- GitHub is source of truth. Local branches/worktrees are agent sandboxes.
- Every patch needs: tests + a clean run command + a metric improvement OR an observability improvement + no secret/package regression.
- CI artifacts: trace summary, evaluation report, charts, status.md updates.
- `reports/status.md` is the human-readable current state.
- This file (`agent_protocol.md`) is the compact instruction file for future agents.

## Merge rule

Merge ONLY if:
- `make verify` passes (imports, validate_config, no-secrets, policy contract, smoke dry-run, package dry-run).
- The patch either improves a tracked metric (Brier, ECE, BSS-vs-market, cost-per-forecast, parse-failure rate) OR adds observability (new trace field, new monitor panel, new test) without regressing existing ones.
- No secrets leaked into commits or logs.
- No new package dependency without a pinned version + a justification in DECISIONS.md.
- Author email is `robbysneiderman@gmail.com` (no dot). Vercel rejects the dotted variant.

## Hard NOs (will be reverted on sight)

- Multi-agent debate frameworks.
- Fine-tuning a forecasting LLM.
- Deep RL trading policy.
- Broad web crawling.
- Discord scraping (Discord Dev Policy prohibits training on message content).
- Live online weight updates without confirming organizer rules first.
- Letting the model choose trade size.
- Hardcoded API keys anywhere in the repo.
- Banned phrases in any new file: `delve`, `leverage`, `navigate`, `embark`, `tapestry`, `realm`.
- Citing Rob's submitted-but-not-accepted papers as if they were published.

## Monitoring gates (every run must produce)

- 100% trace completeness on the 21+ field JSONL schema.
- ≤ 2% JSON parse failure rate.
- Cost under the per-tick / per-eval caps.
- Brier ≥ market-only on the offline holdout, ideally ahead on selected domains.
- ECE stable or improving by price bucket.
- 0 risk-rule violations.

## Test ladder (must climb in order)

| Level | Test | Command |
|---|---|---|
| 0 | imports + config + risk constants | `python -c "import agent, risk, forecaster"` |
| 1 | dry-run startup | `python agent.py --slug smoke --dry-run` |
| 2 | toy replay | `python examples/monitoring_demo.py` |
| 3 | offline pastcast | `python tools/evaluate_offline.py` |
| 4 | package dry run | `python tools/package_submission.py --dry-run` |
| 5 | live test slug when available | `python agent.py --slug rob-live-smoke --once` |
| 6 | final clean machine test | `unzip submission.zip && run one command` |

Don't skip levels. Don't run level 5 until 0-4 are all green.

## Repo structure (canonical)

```
prophet-hacks/
├── agent.py                  # BenchmarkSession lifecycle + main loop
├── forecaster.py             # 7-stage pipeline orchestrator
├── risk.py                   # locked constants + invariants
├── logger.py                 # JSONL trace writer
├── market_filter.py          # eligibility checks
├── config.yaml               # mirrors risk constants for visibility
├── forecasting/
│   ├── market_blend.py       # Kalshi guards + credibility blend
│   ├── source_scoring.py     # credibility hierarchy + staleness
│   ├── source_gate.py        # WHEN to retrieve
│   ├── bidirectional.py      # p_yes + p_no combine
│   ├── expert_pool.py        # Hedge multiplicative weights
│   ├── borrowed_strength.py  # SAE-style composed estimator (v4)
│   ├── domain_pools.py       # Fay-Herriot per (model, domain)
│   ├── reliability_tracking.py
│   ├── sae_shrinkage.py      # explicit logit-additive form (v6)
│   ├── uncertainty.py        # variance aggregation + tier action
│   ├── risk_helpers.py       # required_edge_with_kalshi_adjustment
│   └── prompts.py            # canonical structured-output prompt
├── evaluation/
│   ├── brier.py
│   ├── ece.py
│   ├── returns.py            # incl. pnl_by_price_bucket
│   ├── reliability.py
│   ├── compare_variants.py
│   └── no_leakage_check.py
├── tools/
│   ├── evaluate_offline.py
│   ├── check_compliance.py
│   ├── monitor_run.py
│   ├── ask_project.py        # CLI RAG over Ops/Research/Evidence
│   └── package_submission.py
├── scripts/
│   ├── run_offline_eval.py
│   ├── run_monitor.sh
│   ├── build_pastcast_dataset.py
│   └── summarize_eval.py
├── monitor/
│   ├── live_monitor.py
│   └── template.html.j2
├── connectors/
│   ├── kalshi.py             # optional read-only research connector
│   └── polymarket.py         # optional cross-market comparison
├── offline/
│   ├── dataset_schema.md
│   └── sample_tasks.jsonl
├── examples/
│   ├── toy_events.jsonl
│   └── monitoring_demo.py
├── tests/
│   ├── test_market_blend.py
│   ├── test_source_scoring.py
│   ├── test_bidirectional.py
│   ├── test_evaluation.py
│   ├── test_no_leakage.py
│   ├── test_expert_pool.py
│   ├── test_borrowed_strength.py
│   ├── test_sae_shrinkage.py
│   ├── test_source_gate.py
│   └── test_*.py             # (existing Gemini-era tests stay)
├── docs/
│   ├── RUNBOOK.md
│   ├── DECISIONS.md
│   ├── PRE_EVENT_CHECKLIST.md
│   ├── KALSHI_FINDINGS.md
│   ├── V3_OFFLINE_HARNESS.md
│   ├── BORROWED_STRENGTH.md
│   ├── MONITOR.md
│   └── ARCHITECTURE_V2.md
├── reports/
│   └── status.md             # human-readable current state
├── .github/workflows/
│   ├── verify.yml            # imports + smoke + package check
│   └── offline-eval.yml      # nightly variant comparison + artifact upload
└── agent_protocol.md         # this file
```

## Make targets

```
make verify        # full pre-commit gate
make offline-eval  # PROPHET_OFFLINE_MOCK=1 by default
make monitor       # serve live dashboard at localhost:8765
make compliance    # pre-submission gate
make status        # refresh reports/status.md
make smoke         # python agent.py --slug smoke --dry-run
make test          # pytest tests/ -v
```

## When in doubt

Read `reports/status.md`, then this file, then the relevant doc in
`docs/`. If you still don't know, surface the question to Rob via PR
comment — don't guess at policy.
