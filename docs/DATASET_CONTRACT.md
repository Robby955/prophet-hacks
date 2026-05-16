# Dataset contract and preparation

How we read whatever organizers ship us. Locked Friday 2026-05-15
based on the v6 playbook + ai-prophet-datasets repo shape as of May
2026.

## What we know

- **Forecasting tasks** come from `ai-prophet-datasets` releases via:
  ```
  prophet forecast retrieve -o events.json
  prophet forecast retrieve --dataset hackathon-day --release 2026-05-12 -o events.json
  ```
- Underlying release artifact is line-oriented `tasks.jsonl` inside a
  release folder:
  ```
  datasets/<dataset>/dataset.json
  datasets/<dataset>/releases/<release_id>/release.json
  datasets/<dataset>/releases/<release_id>/tasks.jsonl
  registry.json
  ```
- **Required task fields**: `task_id`, `title`, `outcomes`.
- **Optional fields**: `source`, `context`, `metadata`, `resolved_outcome`.
- `resolved_outcome` is a dict with `value` (list of strings),
  `resolved_at`, `source`. `value` is ALWAYS a list, even for one outcome.
- **Wire-format the predictor returns** per task:
  ```json
  {"p_yes": 0.72, "rationale": "..."}
  ```
- **Scoring**: Brier, `p_yes` constrained to `[0.01, 0.99]`. Random
  baseline = 0.25. Resolved YES = 1.0, NO = 0.0.

- **Trading tasks** come from live 15-minute server snapshots via
  `ai-prophet-core`, NOT from a downloaded file. The server owns
  experiment / tick / fill / portfolio / PnL state.

## What we DO NOT assume

- Only `["Yes", "No"]` strings (also handle `Above/Below`, `Cut/Hold/Hike`,
  named candidates, etc.).
- Only one market per event.
- Only one schema version.
- Only unresolved events at submission time.
- Complete metadata on every task.
- Only Kalshi-style tickers.
- One specific file layout.

## What we do not yet know

- Final event slate size (10 tasks? 500?).
- Final domain distribution.
- Final metadata completeness (will every task carry
  `market_implied_p_yes`? `close_time`? `category`?).
- Whether organizers pin a specific release.
- Whether hosted-endpoint collection is required.
- Whether multi-outcome tasks appear in the final slate.

## How we prepare (already built)

| Concern | File |
|---|---|
| Canonical internal shape | `forecasting/schema.py` (`ForecastTask`) |
| Multi-shape adapter | `forecasting/normalize.py` |
| File-aware loader | `forecasting/dataset_loader.py` |
| Pre-flight inspector | `tools/inspect_events.py` |
| Pre-flight validator | `tools/validate_events.py` |
| Fixture generator | `tools/make_mock_events.py` |
| Offline evaluator | `tools/evaluate_events.py` |
| Toy fixtures | `data/fixtures/{toy_binary, toy_resolved, toy_multi_outcome, toy_bad_schema}_events.jsonl` |
| Unit tests | `tests/test_event_normalizer.py`, `test_dataset_loader.py`, `test_prediction_output_schema.py` |

## Trace fields locked into every prediction

Every JSONL row written by the predictor MUST include:

```json
{
  "dataset": "hackathon-day",
  "release": "2026-05-12",
  "task_id": "KXBTC-25MAR21-B90000",
  "raw_event_hash": "sha256:...",
  "schema_version_seen": "dataset-v1",
  "p_yes": 0.72,
  "rationale": "...",
  "variant": "calibrated-ensemble"
}
```

`raw_event_hash` comes from `forecasting.schema.compute_raw_hash`. It
is stable across JSON key reordering so identical inputs always hash
to the same string. This is the reproducibility lever — given a hash,
we can re-pull the exact raw event from the release and re-run any
variant against it.

## When live data drops, the kickoff sequence

```bash
# 1. Pull the release
prophet forecast retrieve -o events.json

# 2. Inspect shape
python tools/inspect_events.py events.json

# 3. Strict validate
python tools/validate_events.py events.json

# 4. Smoke test the forecaster against the slate
python agent.py --slug live-smoke --once

# 5. (optional) score predictions if any tasks resolve during prep
python tools/evaluate_events.py \
    --events events.json \
    --predictions predictions.json
```

If any of steps 2-4 fail, we have a clean schema fingerprint of the
failure to feed back to organizers. We do not start the continuous
loop until inspect + validate are both green.

## Edge cases the layer already handles

- **JSON list vs wrapper object vs single-event dict** — `unwrap_payload`
  normalizes all three.
- **`.json` vs `.jsonl` vs release-directory vs sharded directory** —
  `dataset_loader.load()` auto-detects.
- **Alternate ID fields** (`id`, `event_id`, `market_id` instead of
  `task_id`) — `normalize.py` accepts the first present.
- **Alternate title fields** (`question`, `name` instead of `title`)
  — same.
- **Outcomes as `[{"name": "X"}, ...]`** instead of `["X", ...]` —
  flattened to strings.
- **Duplicate outcomes** — deduplicated; if the dedup leaves < 2
  outcomes, raises `SchemaError`.
- **YES-label ambiguity** on binary tasks (e.g. `["Mahomes", "Allen"]`)
  — falls back to `outcomes[0]` and flags via
  `metadata.yes_label_inference = "ambiguous_fallback_outcomes_0"` so
  the trace can record it.
- **Multi-outcome tasks** — preserved with `is_binary=False`,
  `yes_label=None`. Caller can opt-in to multi-outcome scoring via the
  optional `p_distribution` field on `PredictionOutput`.
- **Resolved value outside declared outcomes** — raises immediately;
  this is almost always organizer data error.

## What I'd build NEXT (in priority order)

1. `agent.py` integration: call `dataset_loader.load()` at startup,
   normalize once, pass `ForecastTask` everywhere downstream. (Currently
   `agent.py` still consumes raw market dicts from the SDK.)
2. Live-server adapter (`forecasting/normalize.py:ServerAdapter` — TODO).
   Reads the POST body shape the Prophet Arena CLI sends when using
   `--agent-url` and converts to `ForecastTask`.
3. Hashed-event cache: `data/event_cache/<dataset>-<release>/<hash>.json`
   so we can replay a specific tick without re-pulling. Useful for
   debugging the live window from a tracked event after the fact.
4. CI workflow `.github/workflows/dataset_smoke.yml`: runs
   `inspect_events` + `validate_events` against `data/fixtures/*` on
   every push so regressions get caught.

## Anti-patterns explicitly

- Hardcoding `["Yes", "No"]` anywhere in the predictor pipeline.
- Treating `events.json` as the only input shape.
- Discarding `metadata` between normalize and trace — it carries the
  market_implied_p_yes prior and the close_time horizon.
- Silently coercing a non-binary task into a binary `p_yes` without
  flagging it.
- Bypassing the validator on the assumption that "the slate is fine."
  Always validate the slate; the cost is one second, the upside is
  catching a malformed release before the agent burns ticks on it.
