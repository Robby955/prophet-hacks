# CanadaHacks — Prophet Hacks 2026 forecasting submission

*Team: **CanadaHacks** · Track: Forecasting · Author: Rob Sneiderman ([@Robby955](https://github.com/Robby955))*

---

## Inspiration

Forecasting prediction markets is a statistics problem dressed up as a software problem. The flashiest agents lose to the most **calibrated** ones — the ones that know not only *what* they predict but *how much they should believe it*. That intuition came directly from my ICML 2026 work on equivalence testing, where the hardest part is never the model — it's the discipline of asking "is this difference real, or am I fooling myself?"

Prophet Arena's benchmark is a perfect testbed for that mindset. The submission is graded on **Brier score**:

$$
\text{Brier} \;=\; \frac{1}{N}\sum_{i=1}^{N}\bigl(p_i - y_i\bigr)^2
$$

It rewards probabilities that match reality, not just the right side of 0.5. A model that says 0.55 and is right scores worse than a model that says 0.90 and is right — and *much* worse than a model that says 0.90 and is wrong. That asymmetry is the whole game. Our default competitive stance, set before kickoff in our `v7 competition landscape addendum`, was simple: **don't be the flashiest agent, be the most calibrated, monitored, source-aware, market-aware one.** A boring system that always knows why it acted beats a dramatic one that can't diagnose its errors.

## What we learned

1. **Forecasting and trading are different tracks.** We spent the morning building tick-trading resilience patterns before realizing the actual submission was the forecasting track. The cost wasn't wasted — `agent.py`, `risk.py`, and the server-aligned ruleset import are real engineering — but the lesson is sharper: **read the developer portal page before reading the SDK source.** That's in our memory file for next time.

2. **LLMs are usefully calibrated on real prediction markets**, not just toy benchmarks. On the 26-event `sample-resolved` set, a single Sonnet 4.6 call per event scored Brier **0.190**, beating a uniform-prior baseline of 0.219 and a random-0.5 reference of 0.250. That 13% lift over the uninformed prior, on real resolved events, is the concrete evidence that this isn't a vibes-based exercise.

3. **Boundary cases are where calibration dies.** Our agreement gate had a quiet bug: `abs(0.60 - 0.5)` evaluates to `0.09999999999999998` in IEEE-754, so a naive `< 0.10` check rejected the exact-bucket case the spec was explicit about admitting. One padded epsilon, one test case, one decisions-log entry. The bug is small. The discipline is the point.

4. **Server-side rules are the authoritative source.** Our hard caps were drifting from the SDK's `ai_prophet_core.ruleset` module — we hadn't mirrored `MAX_TRADES_PER_DAY`, `MAX_GROSS_EXPOSURE`, or the 9-minute submission deadline. Wiring an import-time assertion (`our caps ≤ server caps`) means any future drift gets caught on `import risk`, not at first rejected intent.

5. **Cross-vendor ensembles probably matter more than chasing a single stronger model.** When Sonnet 4.6 and GPT-5.5-pro disagree, that's signal — the conviction is genuinely lower than either model alone would express. A logit-mean blend preserves probabilistic semantics:

$$
p_{\text{blend}} \;=\; \sigma\!\Bigl(\tfrac{1}{2}\bigl(\mathrm{logit}(p_a)+\mathrm{logit}(p_b)\bigr)\Bigr)
$$

Final ensemble vs single-model numbers landed in `data/predictions/backtest_summary.json` and the live status page at `docs/STATUS.html`.

## How we built it

**Architecture, in order of dependency:**

```
ai_prophet_core.ruleset      (server-authoritative constants)
        ↓
risk.py                      (our caps; import-time asserts ≤ server caps)
forecast_track.py            (5 variants, all returning {p_yes, rationale})
        ↓
scripts/build_actuals.py     (resolved events → {market_ticker: 1.0|0.0})
scripts/backtest_forecast.py (run variants, persist, evaluate, compare)
        ↓
data/predictions/*.json      (per-variant submissions + summary)
```

**Variants exposed (in `forecast_track.py`):**

| Variant | Model | Brier (n=26) | Notes |
|---|---|---|---|
| `predict_uniform_prior` | — | 0.219 | Deterministic `1/len(outcomes)`. Free control. |
| `predict_single_llm` | Sonnet 4.6 | 0.190 | One Anthropic call + JSON-parse fallback chain. |
| `predict_opus_47` | Opus 4.7 | *see backtest_summary.json* | Stronger reasoner, ~3× cost. |
| `predict_gpt55` | GPT-5.5-pro | *see backtest_summary.json* | Cross-vendor sanity check. |
| `predict_ensemble_logit` | Sonnet 4.6 + GPT-5.5-pro | *see backtest_summary.json* | Logit-mean blend. |

**Discipline we shipped, not just claimed:**

- 61 unit tests covering the agreement gate (8 cases), Brier-relevant helpers, hard caps, gross exposure, conflicting positions. `./scripts/agent/verify.sh` is the merge gate.
- An append-only `docs/DECISIONS.md` with 11 entries — every consequential choice has a rationale paragraph.
- A `docs/STATUS.yaml` artifact that any agent (Codex, future Claude runs) can read to pick up the thread.
- Idempotent JSONL traces for every decision, so a crash mid-tick still leaves a recoverable trail.

## Challenges

**Track confusion (the most expensive bug).** Both Prophet Arena tracks share an SDK, a config style, and the same `prophet` CLI binary. The trading track's `BenchmarkSession` lifecycle is conspicuous in the SDK source; the forecasting track's `prophet forecast` subcommand tree is one `--help` invocation away. We optimized the wrong thing for two hours before correcting course. The cure was reading `prophetarena.co/developer` — which we should have done at minute one.

**Variable outcomes lists.** Events in the sample-resolved set have outcome lists ranging from 2 (a tennis match) to 30 (a championship bracket). The `Prediction` schema is a single `p_yes ∈ [0.01, 0.99]`. The binary-encoding interpretation — "is `outcomes[0]` the resolved winner?" — wasn't documented anywhere we found; we recovered it by inspecting the example agent, the schema module, and the actual resolved data shapes side-by-side.

**Float-precision in the conviction gate.** Already mentioned in *What we learned*. The fix is two lines; the lesson is "test the boundary cases your spec calls out by name."

**Lazy assumptions about the CLI being on PATH.** The `prophet` binary lives in `.venv/bin/`. A fresh terminal will not find it. Documented in memory + `CLAUDE.md` so future sessions don't repeat the friction.

## Built with

- **Languages & runtime:** Python 3.13
- **Prophet Arena SDK:** `ai-prophet-core==0.1.4`, `ai-prophet==0.1.4`
- **LLMs:** Anthropic Claude Sonnet 4.6 (default), Claude Opus 4.7 (strong-reasoner variant); OpenAI GPT-5.5-pro (cross-vendor ensemble member)
- **Python libs:** `pydantic` 2.x, `python-dotenv`, `pyyaml`, `pytest` 8.x, `matplotlib`, `pandas`
- **Tooling:** Git, GitHub, `gh` CLI, pytest-driven `scripts/agent/verify.sh` gate
- **Docs as code:** Markdown (DECISIONS, RUNBOOK, STATUS) + YAML status artifact + minimal HTML dashboard

Repo (private during sprint, public post-event after review): [`Robby955/prophet-hacks`](https://github.com/Robby955/prophet-hacks).

---

*Reproducibility receipt:*

```bash
git clone git@github.com:Robby955/prophet-hacks.git && cd prophet-hacks
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in PA_SERVER_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
prophet forecast retrieve --dataset sample-resolved --include-resolved -o data/resolved.json
python scripts/build_actuals.py data/resolved.json data/actuals.json
python scripts/backtest_forecast.py --variants uniform_prior,single_llm,opus_47,gpt55,ensemble_logit
```
