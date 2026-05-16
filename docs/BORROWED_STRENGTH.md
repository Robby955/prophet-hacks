# Borrowed Strength for AI Forecasting

This is the v4 framing of the prophet-hacks system — a research-grade
application of Small Area Estimation methodology to LLM-based
prediction-market forecasting.

## Author

**Rob Sneiderman** — UVic M.Sc. Statistics (Small Area Estimation
focus), McGill B.Sc. Mathematics. Indie founder, theorempath.com.

## The thesis (one paragraph)

A single LLM forecast on a thin or noisy prediction market is a
high-variance direct estimator. Empirical evidence (Prophet Arena,
KalshiBench, the Kalshi favorite-longshot paper) shows that frontier
LLMs are useful but systematically miscalibrated, especially on
low-probability contracts. The classical SAE response to high-variance
direct estimators is **borrowed strength**: partially pool the noisy
estimate with related, lower-variance information sources to obtain
a composite estimator with smaller MSE. This document and the
`forecasting.borrowed_strength` module implement that composition for
prediction-market forecasting, with strata for the market prior, a
calibrated LLM ensemble, a domain-level random effect, and a
historical-similar-markets pool.

## The estimator

For a binary prediction-market event \(E\) with market-implied YES
probability \(p_{\text{market}}\):

\[
\hat p_{\text{final}} = \sigma\!\left(
  w_m \cdot \mathrm{logit}(p_{\text{market}})
  + w_\theta \cdot \mathrm{logit}(\tilde p_{\text{model}})
  + w_h \cdot \mathrm{logit}(p_{\text{history}})
\right)
\]

subject to \(w_m + w_\theta + w_h = 1\), where:

- \(\tilde p_{\text{model}}\) is the calibrated LLM ensemble probability
  after the Kalshi longshot guard,
- the weights are credibility-weighted:
  - \(w_\theta = c(s_q, a, h) \cdot \mathrm{e}^{-2 \max(0, \delta_d)}\)
    with \(c(\cdot)\) the credibility function in
    `forecasting.market_blend.credibility`, \(s_q\) source quality,
    \(a\) model agreement, \(h\) horizon weight, and \(\delta_d\) the
    domain-level Brier offset from the per-(model, domain) reliability
    tracker,
  - \(w_h = \min(0.30, 0.05 + 0.005 \cdot n_{\text{history}})\),
  - \(w_m = \max(0.05, 1 - w_\theta - w_h)\).

The favorites no-shrink rule and the uncertainty-aggregate gate are
applied post-blend. See `forecasting.borrowed_strength.borrowed_strength_estimate`
for the exact implementation.

## Why each stratum

### Market prior \(p_{\text{market}}\)

Prediction-market prices are informative aggregators of beliefs. The
Kalshi paper documents that they are not unbiased — the favorite-longshot
bias is real and systematic — but they remain a strong, low-variance
anchor. We never let any other stratum fully overwrite the market
prior (\(w_m \ge 0.05\)).

### Calibrated LLM ensemble \(\tilde p_{\text{model}}\)

We aggregate two or more LLMs (GPT-5.5, Claude Opus 4.7, plus
optionally retrieval-enhanced variants) via per-model trust-weighted
logit median. Then apply the Kalshi longshot guard: when
\(p_{\text{market}} < 0.10\), the LLM cannot push us aggressively
upward without strong source quality AND high model agreement. The
guard is a direct response to the Kalshi paper's >60% loss finding
on contracts below $0.10.

### Domain random effect \(\delta_d\)

Different domains have different reliability profiles for LLM
forecasting. Weather forecasts dominated by official NOAA/NWS
sources are essentially deterministic for the LLM, while
geopolitics is noisy and the LLM should defer to the market
prior more. We track per-(model, domain) running Brier and use
the Fay-Herriot shrinkage estimator:

\[
\hat{B}_{m,d} = \gamma_{m,d} \cdot \bar{B}_{m,d}^{\text{cell}}
              + (1 - \gamma_{m,d}) \cdot \bar{B}_{m}^{\text{marginal}}
\]

with \(\gamma_{m,d} = n_{m,d} / (n_{m,d} + \lambda)\), \(\lambda = 10\).
Thin (model, domain) cells borrow from the model's marginal mean
Brier across all domains; dense cells use their own data. This is
exactly the Fay-Herriot 1979 small-area estimator.

The domain offset \(\delta_d = \hat{B}_{m,d} - \bar{B}_m^{\text{marginal}}\)
damps the model weight on domains where this model has historically
underperformed.

### Historical similar markets \(p_{\text{history}}\)

When this market has many historical analogues (e.g., NFL Week-1
home-team-win markets across past seasons), the empirical resolution
rate on those analogues is itself a forecast. The weight scales with
\(n_{\text{history}}\) and is capped at 0.30 so a long history can't
overpower the current market or the LLM signal.

## Why this is differentiated

Most Prophet Hacks competitors will be ML/CS-trained. They'll build:

- "Ask GPT what happens" (high variance, no calibration)
- Multi-agent debate (theatrical, no measurable gain on Brier)
- Big retrieval pipelines (helpful, but noisy sources can hurt — see
  the Prophet Arena Bitcoin case study)
- Possibly fine-tuned models (overkill for a 30-hour window)

What they will not have:

- A principled statistical framework for **how aggressively to move
  away from a prior**.
- Hierarchical borrowing across domains and horizons.
- Empirical-Bayes-style adaptive weighting tied to live Brier scores.
- A literature-grounded design (SAE has been used in census and survey
  statistics since the 1970s; the methodology is mature and citeable).

This is the angle.

## Workshop-paper framing (post-event)

Target: **Forecasting Workshop @ ICML 2027** (or similar).

Title:

> **"Borrowed Strength for AI Forecasting: Hierarchical Calibration,
> Market Priors, and Ensemble Reasoning in Open-Domain Prediction
> Markets"**

Structure:

1. Introduction — LLM forecasting limitations from Prophet Arena +
   Kalshi paper.
2. Background — SAE / Fay-Herriot / empirical Bayes / James-Stein.
3. Method — the composed estimator above, with explicit identification
   of each stratum.
4. Offline experiments — 8 strategy variants on a pastcast dataset,
   reported with Brier / ECE / BSS-vs-market / simulated return / Sharpe
   / coverage-by-domain.
5. Live experiment — Prophet Hacks 2026 results.
6. Discussion — when does borrowing strength help? When does it hurt
   (e.g., when the domain pool is corrupted by a regime shift)?
7. Limitations — short evaluation window, single hackathon, no
   real-money calibration.

Co-authorship: TBD. Co-authors should be people who contribute
meaningfully to the empirical work or the SAE theory, not honorary.

## Reusable TheoremPath content

The framework decomposes into 4-6 standalone pages on theorempath.com:

- `small-area-estimation` (foundations, EBLUP, Fay-Herriot, James-Stein)
- `hierarchical-shrinkage` (the general principle, with applied examples)
- `empirical-bayes-for-online-learning` (Hedge as empirical Bayes)
- `borrowed-strength-for-ai-forecasting` (the applied bridge, references
  the workshop paper)
- `prediction-market-microstructure` (already drafted in the v3 content
  cluster: bid/ask/spread, favorite-longshot bias, Maker/Taker)
- `model-aggregation-and-ensemble-disagreement` (median in logit space,
  disagreement as uncertainty)

Cross-links to existing pages: `reliability-diagrams-and-expected-calibration-error`,
`expected-value-and-edge-in-forecasting`, `thompson-sampling`,
`anytime-valid-inference`.

## References (working set, verify before citing)

- Rao, J.N.K. & Molina, I. (2015). *Small Area Estimation* (2nd ed.). Wiley.
- Fay, R.E. & Herriot, R.A. (1979). "Estimates of income for small places:
  an application of James-Stein procedures to census data."
  *JASA* 74(366): 269–277.
- Efron, B. & Morris, C. (1973). "Stein's estimation rule and its competitors —
  an empirical Bayes approach." *JASA* 68(341): 117–130.
- James, W. & Stein, C. (1961). "Estimation with quadratic loss."
  *Proc. Fourth Berkeley Symp.* 1: 361–379.
- Pfeffermann, D. (2013). "New important developments in small area estimation."
  *Statistical Science* 28(1): 40–68.
- Gneiting, T. & Raftery, A.E. (2007). "Strictly proper scoring rules,
  prediction, and estimation." *JASA* 102(477): 359–378.
- Prophet Arena paper (citation TBD when dispatch agent extracts).
- Kalshi favorite-longshot paper (citation TBD when dispatch agent extracts).
