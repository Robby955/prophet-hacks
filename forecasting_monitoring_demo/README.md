# Forecasting Monitoring Demo

This is a tiny offline monitoring harness for Prophet-style forecasting agents.
It demonstrates the minimum loop we want before live events are available:

1. Read toy market/event records.
2. Blend market prior + model forecasts.
3. Apply a Kalshi-inspired longshot guard.
4. Compute edge and trade/skip decisions.
5. Write JSONL traces.
6. Produce a Markdown summary and simple chart.

Run:

```bash
python monitoring_demo.py --events toy_events.jsonl --out outputs
```

Expected outputs:

- `outputs/demo_trace.jsonl`
- `outputs/monitor_summary.md`
- `outputs/brier_by_variant.png`
- `outputs/decision_funnel.png`

The script is intentionally stdlib-first. If `matplotlib` is unavailable, it still writes the JSONL trace and Markdown report.
