# Prophet Hacks Status

This is the single artifact to glance at before kickoff. If the numbers
below are improving, we're on track. If the file is vague, we're drifting.

## Snapshot — 2026-05-15 (Friday evening prep)

| Field | Value |
| --- | --- |
| Date | 2026-05-15 |
| Default variant | `market_blend` (calibrated, with Kalshi guards) |
| Last green command | `pytest tests/ -v` (73 pass on `feat/2026-05-15-prophet-architecture-v2`) |
| Current blocker | None |
| Current best offline Brier | _TBD — run `make offline-eval`_ |
| Market-only Brier | _TBD_ |
| ECE | _TBD_ |
| Trace completeness | 100% of v2 fields populated; v3 adds source_quality, model_agreement, horizon_weight, decision_funnel_step |
| Parse failure rate | 0% on synthetic tasks (PROPHET_OFFLINE_MOCK=1) |
| Cost per 100 forecasts | _TBD — no real API spend yet_ |
| Live-monitor URL | http://localhost:8765/live.html (via `bash scripts/run_monitor.sh`) |

## Known red risks

- **Longshot bias on <$0.10 contracts.** Guarded by `forecasting.market_blend.kalshi_longshot_guard`. Buyers of <$0.10 contracts lose >60% on average per Kalshi paper. Don't let LLMs override that without excellent evidence.
- **LLM overconfidence on vivid narratives.** Active shrinkage via the credibility-weighted blend; bidirectional elicitation gated for near-threshold markets.
- **PA_SERVER_API_KEY** not yet provisioned (organizer publishes Saturday morning).
- **Online-learning rule** unclear — `online_learning.enabled = false` until we get clarity on whether mid-evaluation weight updates are allowed.

## Next action

Saturday morning kickoff sequence:

```
cd ~/Projects/theorempath/prophet-hacks
git pull
python agent.py --slug smoke --dry-run        # Level 1: imports/config
python agent.py --slug live-smoke --once      # Level 5: one real tick
bash scripts/run_monitor.sh                   # in another terminal
python agent.py --slug rob-v1                 # continuous, watch the monitor
```

If `live-smoke --once` is green and the monitor renders, switch to continuous.

## File map

- **Lifecycle / control plane**: `agent.py` (BenchmarkSession + run_continuous + SIGINT)
- **Forecasting / data plane**: `forecaster.py` (7-stage pipeline) + `forecasting/` (market_blend, source_scoring, bidirectional, expert_pool)
- **Risk gate**: `risk.py` (constants locked; longshot_proximity tightens edge)
- **Logging**: `logger.py` (21+ optional v2/v3 fields, JSONL)
- **Monitor**: `monitor/live_monitor.py` + `monitor/template.html.j2` + `scripts/run_monitor.sh`
- **Offline eval**: `tools/evaluate_offline.py` (8 variants) + `scripts/run_offline_eval.py` (orchestrator)
- **Submission gate**: `tools/check_compliance.py`

## Roadmap

- v0: tick loop + market baseline + logs ✓ (Gemini baseline merged to main)
- v1: GPT-5.5 structured forecast + market blend ✓ (Gemini)
- v2: source scoring + targeted retrieval ✓ (v2 PR #1, awaiting review)
- v3: Opus cross-check + disagreement shrinkage + Kalshi guard ← current branch
- v4: bidirectional elicitation on selected markets (queued)
- v5: full evaluation dashboard / reliability curves (live monitor partially covers this)
