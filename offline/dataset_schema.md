# Offline pastcast dataset schema

Every record in an offline pastcast JSONL file MUST match this shape.
`evaluation.no_leakage_check.assert_no_leakage` will raise on any
record where any source's `published_at` is later than the event's
`forecast_time`.

## Fields

```json
{
  "event_id": "string-unique-per-event",
  "market_id": "string-unique-per-market (one event may have multiple markets)",
  "question": "string — the binary forecasting question",
  "resolution_criteria": "string — the exact rule that decides YES vs NO",
  "domain": "sports|finance|crypto|weather|elections|science|tech|geopolitics|health|other",
  "forecast_time": "ISO-8601 timestamp — the moment of the forecast",
  "resolution_time": "ISO-8601 timestamp — the moment of resolution",
  "yes_bid": 0.42,
  "yes_ask": 0.46,
  "no_bid": 0.53,
  "no_ask": 0.57,
  "market_implied_p_yes": 0.44,
  "sources": [
    {
      "url": "string",
      "title": "string",
      "summary": "string — what we'd give to the LLM",
      "published_at": "ISO-8601",
      "retrieved_at": "ISO-8601",
      "source_type": "official|primary|news|analysis|blog|social|market|unknown",
      "supports_yes": false,
      "supports_no": false
    }
  ],
  "outcome": 0
}
```

## Hard requirements

- `outcome` MUST be `0` or `1`. No nulls. (If the market never
  resolved, exclude the record from the dataset entirely.)
- `market_implied_p_yes` SHOULD equal `(yes_bid + yes_ask) / 2` or the
  data source's quoted mid. If the source doesn't provide one, compute it.
- Every `source.published_at` MUST be `<= forecast_time`. Otherwise
  the no-leakage gate will refuse the dataset.
- `forecast_time` MUST be strictly before `resolution_time`.
- Bids and asks are in $/share (0..1), not in percent.

## Soft conventions

- Prefer `source_type` values from the seven canonical buckets. The
  source-scoring module falls back to `unknown` if it doesn't
  recognize the string, but this loses information.
- `summary` should be ≤ 500 tokens. The LLM's evidence block is
  capped at ~3K tokens total across all sources to keep prompts cheap.
- `domain` is used for coverage-by-domain reports and the
  domain-specific specialists in `market_router`. If you can label
  it, label it; `other` is fine if uncertain.

## Time-based split convention

The harness assumes the dataset is the **train + validation + holdout**
union, sorted by `forecast_time`. `scripts/run_offline_eval.py` will
respect a `split` field if present:

```json
{ ..., "split": "train|validation|holdout" }
```

If `split` is absent, the script does a strict time-based split:
oldest 60% train, next 20% validation, newest 20% holdout. Never
random.

## Where real datasets come from

- Prophet Arena's historical event dumps (`prophet forecast retrieve
  --historical ...` when supported).
- KalshiBench (the 300-question prediction-market calibration
  benchmark — `arXiv:2512.16030`).
- Manual scrape of resolved markets from Kalshi / Polymarket /
  Metaculus with explicit source-time metadata.

For the synthetic Friday-evening dry run we use
`offline/sample_tasks.jsonl` (12 hand-built records spanning the
6 main domains, including <$0.10 longshots and >$0.85 favorites to
exercise the Kalshi guards).
