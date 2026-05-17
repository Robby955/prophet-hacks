## Brier decomposition (Murphy 1973) — production vs alternatives

Brier = Reliability − Resolution + Uncertainty.

- **REL ↓** (reliability error): how miscalibrated bin-conditional
  forecasts are from empirical bin hit-rates.
- **RES ↑** (resolution): how much bin-conditional hit-rates differ
  from the base rate — measures the forecaster's discriminative power.
- **UNC** (irreducible): base-rate variance `p(1-p)`. Same across variants
  on the same dataset (here 0.246).

| Variant | n | REL ↓ | RES ↑ | UNC | BS |
|---|---:|---:|---:|---:|---:|
| Opus 4.7 (production) | 26 | 0.03742 | 0.24852 | 0.24852 | 0.03782 |
| Opus 4.6 | 26 | 0.03879 | 0.24852 | 0.24852 | 0.03913 |
| GPT-5.2 | 26 | 0.04335 | 0.24852 | 0.24852 | 0.04384 |
| GPT-5.5 | 26 | 0.02465 | 0.18185 | 0.24852 | 0.09196 |
| Gemini 3.1 Pro | 26 | 0.04965 | 0.21262 | 0.24852 | 0.08732 |

Reading the decomposition: low REL means the forecaster's stated probabilities track empirical hit rates within bins. High RES means the forecaster sorts winners from losers. Two forecasters with the same Brier can have very different REL/RES profiles.