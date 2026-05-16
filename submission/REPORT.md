# The Oracles — Technical Report

*Team CanadaHacks · Prophet Hacks 2026 · Author Rob Sneiderman · Snapshot 2026-05-16 ~10:05 CT*

This report is the single document a reader can open and understand: what we built, why we built it the way we did, what we measured, and what we still don't know. Companion files:

- `docs/ROADMAP.md` — sprint plan with timeline and tasks
- `docs/STATUS.yaml` — machine-readable status for agent handoffs
- `docs/STATUS.html` — same data, one-glance view
- `docs/DECISIONS.md` — append-only rationale log

---

## 1. The problem

Prophet Arena runs a forecasting benchmark over real-world prediction markets (Kalshi, Polymarket). Each event has a binary YES/NO resolution; participants submit a probability `p_yes ∈ [0.01, 0.99]` per event; submissions are scored by **Brier score**

$$
\text{Brier} = \frac{1}{N}\sum_{i=1}^{N}(p_i - y_i)^2,
$$

where `y_i ∈ {0, 1}` is the resolved outcome. Lower is better. Random 0.5 produces Brier 0.25; perfect produces 0.

Each event ships with:
- a `title` (the question), `description`, `rules`
- a `category` (Sports, Politics, Elections, Entertainment, Economics)
- a `close_time`
- a list of `outcomes` of length 2–30 (we observed up to 30 in `sample-resolved`)
- once resolved: `resolved_outcome.value`, a list naming the winner

We discovered (by inspecting the schema + the example agent) that the implicit binary YES condition is **"is `outcomes[0]` the resolved winner?"**. The `Prediction` schema has a single `p_yes` and the `evaluate` module compares it against `1.0` if YES else `0.0`.

## 2. The system

Five files do the work:

```
forecast_track.py             predict(event) -> {p_yes, rationale}; 5 variants today
scripts/build_actuals.py      resolved.json -> {market_ticker: 1.0|0.0}
scripts/backtest_forecast.py  run variants, persist predictions, run evaluator, compare
scripts/sync_env_from_variables.sh   ~/Desktop/variables.txt -> .env (single source of truth)
scripts/agent/verify.sh       merge gate: mypy + pytest + smoke import + dry-run
```

Each `predict_*` function is a pure function `(event: dict) -> {p_yes: float, rationale: str}`. That makes them composable: the ensemble variant just calls two underlying predictors and blends their outputs.

### 2.1 Variants

| ID | Variant | Approach | Cost / event |
|---|---|---|---|
| V0 | `predict_uniform_prior` | `1/len(outcomes)` clamped to [0.01, 0.99] | $0.00 |
| V1 | `predict_single_llm` | One Sonnet 4.6 call with structured JSON output | ~$0.005 |
| V2 | `predict_opus_47` | One Opus 4.7 call (stronger reasoner, ~3× cost) | ~$0.015 |
| V3 | `predict_gpt55` | One GPT-5.5 call (cross-vendor) | ~$0.01 |
| V4 | `predict_ensemble_logit` | Sonnet 4.6 + GPT-5.5, blended in logit space | ~$0.015 |

The logit-mean ensemble is

$$
p_{\text{blend}} = \sigma\!\left(\tfrac{1}{2}\bigl(\operatorname{logit}(p_a) + \operatorname{logit}(p_b)\bigr)\right),
$$

where `σ` is the sigmoid. This is slightly different from the arithmetic mean for asymmetric splits and respects the probabilistic geometry.

### 2.2 Prompt

A single system prompt enforces a calibration scale (0.50 = no view; 0.70 = real view; 0.90 = near-certain) and forbids 0.01 / 0.99 unless mechanically determined. The user prompt injects `title`, `description`, `category`, `close_time`, the `outcomes` list, the explicit YES condition `outcomes[0]`, and the uninformed prior `1/n`. The model is told to return strict JSON `{"p_yes": float, "rationale": "..."}`. We parse it with a three-stage fallback (direct → embedded regex → number-extraction regex).

### 2.3 Risk + observability

- **Server-authoritative ruleset import** — our `risk.py` imports `ai_prophet_core.ruleset` and asserts at import time that our caps never exceed the server's. Any future drift fails on `import risk` instead of at first rejected intent.
- **Variant-level decision records** — every prediction is written to `data/predictions/<variant>.json` with rationale, prompt hash, and a per-event timestamp. The Brier evaluator runs against `data/actuals.json` and writes `backtest_summary.json`.

## 3. Numbers on real data

`sample-resolved` is a public dataset of 26 resolved Prophet Arena events (Sports 16 · Entertainment 4 · Elections 3 · Politics 3; outcome counts 2–30; balanced 12 YES / 14 NO under the binary encoding).

| Variant | Brier ↓ | vs uniform | vs random |
|---|---:|---:|---:|
| Random 0.5 reference | 0.250 | +0.031 | 0.000 |
| `uniform_prior` | 0.219 | 0.000 | −0.031 |
| **`single_llm` (Sonnet 4.6)** | **0.189** | **−0.030** | **−0.061** |
| `opus_47` | *rerunning* | — | — |
| `gpt55` | *rerunning* | — | — |
| `ensemble_logit` | *rerunning* | — | — |

`single_llm`'s 0.189 corresponds to a **+24% relative improvement vs random** and **+14% vs uniform prior**, on 26 events. The 26-event sample is small — a paired bootstrap on the Brier deltas would give us a confidence interval, which we'll run once the rerun lands.

We do not yet have numbers for V5–V10 in the roadmap (Opus 4.6, GPT-5.2, retrieval, self-consistency).

## 4. What surprised us

- **Track confusion.** We spent ~2 hours porting trading-track resilience patterns into `agent.py`/`risk.py` before realizing the actual submission was the forecasting track. The work isn't wasted (real engineering, portfolio value), but it cost time we'd have spent on Brier improvements. Lesson: read the developer portal page before reading the SDK source.
- **`abs(0.60 - 0.5) = 0.09999999999999998`.** Our agreement gate had `< 0.10` as the exclusion check, which incorrectly excluded the exact-bucket case 0.60 vs 0.60. Float-precision is a known trap; the patch is two lines. The mistake is small. The discipline of writing a test that pinned the boundary value is the point.
- **Multi-outcome events.** Some events in `sample-resolved` had 30 outcomes (championship brackets), but the `Prediction` schema only has a single `p_yes`. We infer the binary YES condition is `outcomes[0]`, and our prompt explicitly tells the model so. This is consistent with both the example agent's behavior and the actual resolved data, but it's worth confirming with the organizers on Discord.
- **API gotchas per vendor.**
  - Opus 4.7 rejects `temperature` (`temperature is deprecated for this model`).
  - GPT-5.x reject `max_tokens` (must be `max_completion_tokens`) AND consume internal reasoning tokens before the visible response — 300-token caps return empty output for nontrivial prompts. We use 2000.
  - `gpt-5.5-pro` is a completion-only model, not chat. Use `gpt-5.5` for chat.

## 5. Open questions

The four we'd ask the organizers if we could:

1. Is the submission path **HTTP-endpoint-only** (server calls our agent via `prophet forecast register --endpoint-url`) or does a one-shot CLI upload of `predictions.json` exist?
2. The leaderboard's main column shows top scores around 0.91–0.94. That can't be Brier (Brier is bounded 0–1, lower better). Is it calibrated accuracy? Log-score? What metric is the **prize** decided on?
3. Are events scored **immediately upon resolution** or in a batch at the end? Affects how aggressively we resubmit during the sprint.
4. Are duplicate predictions across reps allowed? (We registered without `--endpoint-url`; we may need to re-register with one.)

## 6. What's next

Per `docs/ROADMAP.md`:

- Finish the V2/V3/V4 rerun. *Running.*
- Add V5 (Opus 4.6) and V6 (GPT-5.2). Top leaderboard models we have API access to.
- Pick winning variant on a paired-bootstrap test.
- Stand up the submission endpoint OR confirm a file-upload path. Critical-path for actually scoring.
- Iterate on retrieval-augmented (V9) if there's time and the spread between V5/V6 and V1 is meaningful.

---

## Appendix A — Reproducibility

```bash
git clone git@github.com:Robby955/prophet-hacks.git
cd prophet-hacks
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
bash scripts/sync_env_from_variables.sh  # pulls keys from ~/Desktop/variables.txt
prophet forecast retrieve --dataset sample-resolved --include-resolved -o data/resolved.json
python scripts/build_actuals.py data/resolved.json data/actuals.json
python scripts/backtest_forecast.py --variants uniform_prior,single_llm,opus_47,gpt55,ensemble_logit
```

## Appendix B — Tech stack

Python 3.13, `ai-prophet-core` 0.1.4, `ai-prophet` 0.1.4, `anthropic` 0.102, `openai` 2.37, `pydantic` 2.13, `python-dotenv`, `pyyaml`, `pytest` 8.x, `matplotlib`, `pandas`. Standard pinned-dep hackathon stack; no esoteric tools.

## Appendix C — Generate PDF

```bash
pandoc submission/REPORT.md -o submission/REPORT.pdf --pdf-engine=xelatex \
       --variable mainfont="Helvetica" --variable monofont="Menlo"
```

LaTeX math survives `pandoc`'s default markdown → PDF pipeline.
