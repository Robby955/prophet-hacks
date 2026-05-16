# Monitoring Demo Summary

## Decision funnel

- Markets seen: 6
- Forecasted: 6
- Retrieval/high-source-quality cases: 3
- BUY decisions: 1
- SKIP decisions: 5

## Forecast metrics

| Variant | Mean Brier | ECE |
|---|---:|---:|
| Market | 0.1352 | 0.1600 |
| GPT-5.5 | 0.1210 | 0.1800 |
| Opus | 0.1187 | 0.1567 |
| Final blend | 0.1188 | 0.1475 |

## Decisions

| Market | Domain | p_market | p_final | Action | Side | Edge | Note |
|---|---|---:|---:|---|---|---:|---|
| toy_btc_120k | finance_crypto | 0.42 | 0.53 | SKIP |  | 0.075 | edge below threshold; yes_edge=0.075, no_edge=-0.135 |
| toy_sports_favorite | sports | 0.74 | 0.72 | SKIP |  | -0.012 | edge below threshold; yes_edge=-0.048, no_edge=-0.012 |
| toy_longshot_policy | politics | 0.07 | 0.08 | SKIP |  | -0.021 | edge below threshold; yes_edge=-0.021, no_edge=-0.039 |
| toy_weather_95f | weather | 0.61 | 0.71 | BUY | YES | 0.094 | edge cleared |
| toy_award_longshot | entertainment | 0.14 | 0.18 | SKIP |  | 0.008 | edge below threshold; yes_edge=0.008, no_edge=-0.068 |
| toy_macro_cpi | macro | 0.48 | 0.54 | SKIP |  | 0.029 | edge below threshold; yes_edge=0.029, no_edge=-0.089 |

## Interpretation

The demo is intentionally tiny, so the metrics are not statistically meaningful. The goal is operational: confirm that every market produces a trace, every skip has a reason, and the final forecast can be compared against market-only and model-only baselines.
