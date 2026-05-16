# Live monitor

Auto-refreshing HTML view of the JSONL trace. Read-only. No flask, no
websockets — the page reloads itself via a `<meta http-equiv="refresh">`
tag and a background Python process rewrites the HTML every 5 seconds.

Same artifact serves two jobs:
- During the event: glance at the page (laptop or phone on the LAN) to
  see what the agent is doing.
- After the event: hand off as part of the portfolio write-up — every
  panel is computed from a public JSONL schema.

## Run it

```bash
bash scripts/run_monitor.sh
# then open http://localhost:8765/live.html
```

Environment overrides:

| variable                    | default  | what it does                                   |
| --------------------------- | -------- | ---------------------------------------------- |
| `PROPHET_TRACE_PATH`        | `trace`  | Where the agent writes JSONL files.            |
| `PROPHET_MONITOR_REFRESH`   | `5`      | Seconds between re-renders + meta-refresh.     |
| `PROPHET_MONITOR_PORT`      | `8765`   | Static file server port.                       |

Single-shot mode for post-event reporting:

```bash
python monitor/live_monitor.py --once --output traces/final.html
```

## Panels

| panel              | source field(s)                              | notes |
| ------------------ | -------------------------------------------- | ----- |
| Live stats cards   | aggregate of action, notional, cost_estimate_usd, timestamp deltas | Ten cards across the top. |
| Recent decisions   | last 20 records by timestamp                 | Color-coded BUY rows. Renders v2's p_market / p_final if present. |
| Skip reasons       | `skip_reason` (head before first colon)      | Top 8. Reveals which gates fire most. |
| p_yes distribution | `p_yes`                                      | 10 bins over [0, 1]. Check for clustering at 0.50 (model going neutral). |
| Provider mix       | `model_provider`                             | Pie chart. Useful for confirming the ensemble actually invokes both models. |
| Model disagreement | `disagreement_stdev` (v2 only)               | Empty panel before v2 lands. Highlights ensemble dispersion. |

All field reads use `.get()` and tolerate the legacy schema, so the
monitor works against Gemini-baseline traces and v2 traces interchangeably.

## Schema dependence

Records the monitor expects to find on every row:

- `timestamp` (ISO 8601 UTC)
- `tick_id`
- `action` (`BUY` | `SKIP`)

Everything else is optional and a missing field downgrades a panel
gracefully rather than failing the render. Malformed lines (e.g. a half-
written record during a hard kill) are skipped with a debug log.

## Extending

The aggregation is a pure function (`monitor.live_monitor.aggregate`)
that takes an iterable of dicts and returns the `Aggregate` dataclass.
Add a field, then plug a new chart function — the template is the only
place that wires panel data to layout. Tests live in
`tests/test_live_monitor.py`.
