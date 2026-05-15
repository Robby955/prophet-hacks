# Architecture v2 — Calibrated Ensemble Forecaster

This is the locked v2 architecture for the Prophet Hacks agent. It is a
market-aware, retrieval-disciplined, calibrated ensemble forecaster with
strict logging and risk gating. It is **not** a multi-agent debate
system. The code, not any model, computes the final probability.

Memory file: `~/Library/Application Support/Claude/local-agent-mode-sessions/.../agent/memory/project_prophet_hacks_strategy.md`

## Pipeline

Seven stages, composed left to right. Each stage takes the working
candidate dict and returns it enriched. Pure functions where possible.

```
+----------------+    +----------------+    +-------------------------+
| market_router  | -> | retrieval_gate | -> | decomposition_forecaster |
+----------------+    +----------------+    +-------------------------+
                                                       |
                                                       v
+-----------+    +-----------+    +-----------------------+
| risk_gate | <- | calibrator| <- |  ensemble_forecaster  |
+-----------+    +-----------+    +-----------------------+
```

1. **market_router** (`market_router.py`) — Regex and keyword lookup over
   the question and resolution criteria. Classifies the market into one
   of `sports / finance / weather / elections / science_tech /
   geopolitics / other`, returns `horizon_hours` and `resolution_type`.
   No LLM call.
2. **retrieval_gate** (`retrieval.py`) — `should_retrieve(market)`
   decides whether the model needs outside info. When yes, fetches one
   to three high-quality sources following the credibility hierarchy
   (`official > primary > news > analysis > social`). On Saturday the
   stub builds a single primary source from the market description and
   resolution criteria, with the same shape the production fetcher
   returns. No broad crawl.
3. **decomposition_forecaster** (`decomposition.py`) — Asks the triage
   model to break the market into sub-events with marginal
   probabilities and a combination rule, then states `p_yes` and a
   short rationale. Strict JSON schema, validated by `json.loads`. On
   parse failure the module returns a forecast shrunk toward the market
   price rather than crashing.
4. **ensemble_forecaster** (`ensemble.py`) — Combines model forecasts
   via median-of-logits. Disagreement above the threshold (population
   stdev > 0.12) forces a hard shrink toward the market price. Strong
   models are only called when the triage model crosses the edge gate
   versus the market; a second strong model fires only when the first
   crossed the gate by at least 1.5x.
5. **calibrator** (`calibrator.py`) — Logit-space pooling between the
   market price and the model forecast, with weights driven by evidence
   quality. A final tau shrink keeps the result conservative.
6. **risk_gate** (`risk.py`) — Computes `yes_edge`, `no_edge`,
   `alpha_vs_market`, and `executable_edge`. The downstream order
   builder gates trades on `EDGE_THRESHOLD = 0.08` and the locked risk
   constants.

## Math primitives

```python
# Numerically stable logit / sigmoid (calibrator.py)
def logit(p): return math.log(q / (1 - q))  # q clamped to (eps, 1-eps)
def sigmoid(x):
    if x >= 0: return 1 / (1 + math.exp(-x))
    return math.exp(x) / (1 + math.exp(x))

# Ensemble (ensemble.py)
ensemble_prob(probs)        = sigmoid(median([logit(p) for p in probs]))
disagreement_penalty(probs) = pstdev(probs)

# Calibrator blend (calibrator.py)
w_model  = 0.20 + 0.45 * evidence_quality
w_market = 1.0 - w_model
pooled   = sigmoid(w_market * logit(p_market) + w_model * logit(p_model))
p_final  = p_market + tau * (pooled - p_market)        # tau = 0.75
```

## Risk math primitives (risk.py)

```python
alpha_vs_market(p_final, p_market) = p_final - p_market

# Uses actual top-of-book prices, not the midpoint
executable_edge(p_final, bid, ask) = max(
    p_final - ask,                   # buy YES at the ask
    (1 - p_final) - (1 - bid),       # buy NO at the bid
)
```

## Decomposition JSON schema

The triage model returns ONLY the JSON object below. The pipeline parses
it with `json.loads` and validates required keys. On any parse or
validation failure the decomposition module returns a fallback with
`parse_failed=True` and a `p_yes` shrunk toward the market price.

```json
{
  "sub_events": [
    {
      "id": "string",
      "statement": "string",
      "p": 0.0,
      "depends_on": ["string", "..."],
      "rationale": "string"
    }
  ],
  "combination": {
    "rule": "all_of | any_of | chain | custom",
    "rule_detail": "string"
  },
  "p_yes": 0.0,
  "rationale": "string"
}
```

## Credibility hierarchy

| Source type | Weight | Examples |
|-------------|--------|----------|
| official    | 1.00   | leagues, central banks, election commissions |
| primary     | 0.85   | company filings, candidate statements |
| news        | 0.70   | AP, Reuters, FT, NYT, BBC |
| analysis    | 0.45   | newsletters, blog posts from analysts |
| social      | 0.20   | tweets, forum threads, unverified rumor |

Evidence quality aggregates credibility-weighted, staleness-discounted
items and caps at the count of contributing sources. Three fresh, well
credentialed sources approximate quality 1.0.

## Locked risk constants

| Constant | Value |
|----------|-------|
| EDGE_THRESHOLD | 0.08 |
| MAX_MARKETS_ANALYZED_PER_TICK | 5 |
| MAX_TRADES_PER_TICK | 3 |
| MAX_NOTIONAL_PER_NEW_POSITION | 100.0 |
| MAX_OPEN_POSITIONS | 30 |
| MAX_NOTIONAL_PER_MARKET | 1000.0 |
| MAX_MODEL_CALLS_PER_TICK | 8 |

These are LOCKED. Raising them requires explicit approval and a new
DECISIONS.md entry.

## Metrics we track per tick

- `p_market`, `p_model_raw`, `p_model_shrunk`, `p_final`
- `disagreement_stdev`
- `evidence_quality`, `evidence_sources`
- `yes_edge`, `no_edge`, `alpha_vs_market`, `executable_edge_yes`
- `domain`, `horizon_hours`, `resolution_type`
- `decomposition_json` (preserved for offline replay)
- `skip_reason_detailed`
- `confidence_bucket` (low / medium / high)
- `cost_estimate_usd`

## Paper queue (ranked 1–10)

1. Tetlock & Gardner, _Superforecasting_ — calibration tables, base
   rates, reference classes.
2. Platt scaling and isotonic regression — classical calibration of
   binary classifiers.
3. Brier (1950) — proper scoring rule used by Prophet Arena.
4. Roulston & Smith (2002) — combining forecasts via likelihood pooling.
5. Genest & Zidek (1986) — logarithmic pooling of expert probabilities.
6. Allard et al. (2012) — properties of aggregated probability
   forecasts; lessons for our median-of-logits choice.
7. Ranjan & Gneiting (2010) — calibrated, sharp, optimal probabilistic
   forecasts.
8. Yan et al. (2024) — LLM probability calibration; raw model
   confidence is poorly calibrated and must be re-shaped.
9. Robust statistics primer for the median estimator — why median over
   logits beats mean of probs under heavy-tailed disagreement.
10. Domain prior literature (sports, finance, weather) — light-touch
    references for per-domain horizon and base-rate priors.

## Anti-patterns (do NOT implement)

- Multi-agent debate. We use an ensemble with explicit disagreement
  handling, not chained adversarial agents.
- Fine-tuning. Not within budget or time and adds little for a single
  24-hour event with no holdout.
- Broad web crawling. Retrieval is narrow and allowlist-driven; one to
  three sources per market.
- Letting any model compute the final probability. The code in
  `calibrator.blend_forecast` produces the number that goes into the
  edge check.
- Raising the locked risk constants under pressure.
- Trusting raw LLM probability confidence. Always shrink and blend
  against the market price.

## Pointers

- Code: `forecaster.py`, `calibrator.py`, `ensemble.py`,
  `market_router.py`, `retrieval.py`, `decomposition.py`, `risk.py`,
  `logger.py`, `agent.py`.
- Tests: `tests/test_calibrator.py`, `tests/test_ensemble.py`,
  `tests/test_market_router.py`, `tests/test_pipeline_integration.py`.
- Older decisions: `docs/DECISIONS.md`.
