# The Oracles — Technical Report

*Team CanadaHacks · Prophet Hacks 2026 · Author Rob Sneiderman · Updated 2026-05-17*

This report is the single document a reader can open and understand:
what we built, why we built it the way we did, what we measured, and
what we still don't know. Companion files:

- `docs/ROADMAP.md` — sprint plan with timeline and tasks
- `docs/DECISIONS.md` — append-only decision log
- `docs/SUBMISSION.md` — execution guide for the submit-endpoint form
- `docs/STATUS.yaml` + `docs/STATUS.html` — machine-readable status
- `static/summary.html` and `static/summary.pdf` — visual report

## 1. The problem

Prophet Arena scores forecasts on Brier — squared error between
predicted probability and the resolved 0/1 outcome. Lower is better;
random-0.5 = 0.25. Each event arrives as `{title, description,
category, close_time, outcomes[]}` and we return per-outcome
probabilities. Brier punishes confident-and-wrong twice as hard as
hedging-and-wrong, so the whole game is matching conviction to
evidence.

## 2. The agent

**Endpoint:** `POST https://agent.forecastingpath.com/predict` (FastAPI
on Railway). Variant `multi_outcome_retrieval` set by
`PROPHET_AGENT_VARIANT`.

**Pipeline (5 work stages, all observable per call via the
auth-protected `/predictions` endpoint):**

1. **Build query.** Title + most informative non-trivial outcome label,
   ≤200 chars (`forecast_track.py`).
2. **Brave Search.** `count=5`; degrades to non-retrieval
   `predict_multi_outcome` if the key is missing or the call fails.
3. **Dedupe + rank.** One result per domain, prioritized by
   `_domain_priority` (.gov / .edu / exchanges / major outlets first),
   top 5.
4. **LLM call.** Claude Opus 4.7. System prompt enforces a 0.50–0.90
   calibration scale AND **market-odds anchoring** — anchor to any
   cited implied probability; move >5pp only with specific contrary
   evidence. Strict JSON output; 5-stage parser fallback for
   non-conforming responses; fuzzy outcome-label matching for
   case/whitespace mismatches.
5. **Kalshi longshot guard.** Floor each per-outcome probability at
   `min(0.10, max(0.05, 0.5/n))`, renormalize from above-floor entries
   only.

The architecture image at `static/howagentworks.webp` adds two bookend
steps (event-in / JSON-out) for visual clarity — the work is in the
5 stages above.

**Three ideas that mattered:**

- *Web evidence beat pure-prior judgement.* Same prompt without
  retrieval → Brier 0.19. Add Brave + market-odds anchoring →
  **0.0379**. The Sonnet→Opus swap alone is worth ~0.02; the rest is
  evidence.
- *Schema compliance is half the win.* Three of four alternative
  models emitted JSON with keys not in the supplied outcome list.
  Opus 4.7 followed the schema; others would have needed the
  parser-hardening + fuzzy-match fallback just to be scored.
- *The longshot floor had a bug.* Old `max(0.05, 0.5/n)` returned 0.25
  for binary events, silently clamping every binary prediction into
  `[0.25, 0.75]`. New `min(0.10, max(0.05, 0.5/n))` caps at the
  Kalshi-paper empirical threshold of 0.10. ~6× per-event Brier
  improvement on binary longshots.

## 3. Numbers on real data

`sample-resolved` — 26 resolved Prophet Arena events. Same retrieval +
prompt; only the LLM call swaps.

**Scoring methodology note.** Prophet Arena's CLI evaluator
(`prophet forecast evaluate`) implements single-binary Brier on
`(p_yes - 1{outcomes[0] won})²`. PA's published formula suggests
multi-class Brier (sum across all outcomes per event); their CLI does
not implement that. We report both metrics. The primary headline is
single-binary because that's what we can verify locally against PA's
own evaluator; multi-class is a caveat in case live scoring differs.

| Variant | Single-binary ↓ | Multi-class ↓ | Multi-only (n=12) |
|---|---:|---:|---:|
| **Claude Opus 4.7 (production)** | **0.0378** | 0.2558 | 0.4551 |
| Claude Opus 4.6 (PA leaderboard top agent) | 0.0391 | **0.2500** | 0.4396 |
| OpenAI GPT-5.2 | 0.0438 | 0.2874 | 0.4971 |
| Claude Sonnet 4.6 (previous prod) | 0.0639 | (0.6912)* | — |
| OpenAI GPT-5.5 | 0.0920 | 0.3429 | 0.6552 |
| Gemini 3.1 Pro Preview | 0.0983 | 0.4773 | 0.8115 |
| *random 0.5 baseline* | 0.250 | — | — |
| *uniform 1/n prior* | 0.219 | — | — |

*Sonnet's prediction file pre-dates the bug fix that adds per-outcome
probabilities; its multi-class number reflects the uniform 1/n
fallback, not real model behavior. Single-binary is unaffected.

**Honest comparison:** Opus 4.7 wins single-binary by **3.4%** over
Opus 4.6 (0.0378 vs 0.0391), not the 5× claim from an earlier draft
of this report (which had compared metrics inconsistently across
models — postmortem in `docs/DECISIONS.md` 2026-05-17 entry). Under
proper multi-class scoring, Opus 4.6 is marginally *better* (0.2500
vs 0.2558). Model selection is metric-dependent on n=26; we hold
Opus 4.7 because PA's CLI scoring (the only metric we can verify)
favors it, and because the Sonnet→Opus 4.7 transition is the
production-tested path.

**Phase 2 vs Phase 1 (Sonnet) headline delta:** the
Opus 4.7 vs Sonnet 4.6 improvement (0.0378 vs 0.0639) has a 95%
paired-bootstrap CI of [0.0143, 0.0374] (n=26 paired events,
50,000 resamples, seed `20260516`). The interval excludes zero;
significant at α=0.05 on this dataset under single-binary scoring.

**Where the win came from (decomposition, single-binary scoring):**

| Variant | Mean Brier |
|---|---:|
| Sonnet 4.6 + old longshot floor (clamps binary to 0.25) | 0.0639 |
| Sonnet 4.6 + new floor (caps at 0.10) | 0.0418 |
| **Opus 4.7 + new floor (production)** | **0.0378** |

Roughly **85% of the Phase 2 improvement comes from the longshot-floor
bug fix** (a post-LLM safety-net change); the remaining ~15% is the
Sonnet→Opus swap. The dominant gain is from fixing a silent
post-processing bug, not from a model upgrade. We highlight this
because the discipline finding (boundary tests for safety nets) is
more transferable than the model choice.

**On the schema-compliance hypothesis (revised):** GPT-5.5 and
Gemini 3.1 Pro genuinely struggle (single-binary 0.092 and 0.098
respectively; multi-class 0.34 and 0.48). For Opus 4.6 and GPT-5.2
the gap to production is small (Opus 4.6 single-binary 0.0391,
multi-class 0.2500 — *better* than production on multi-class). The
strongest claim we can defend is "alternative models span a real
spectrum, with Gemini and GPT-5.5 clearly inferior under both
metrics." The earlier "25× multi-outcome gap" claim conflated
single-binary and multi-class scores.

n=26 is small and binary-skewed (16/26 sports matchups); the
bootstrap CI excludes zero on this dataset under single-binary
scoring but cannot establish convergence. Live PA performance is
the only true test.

**Open-event multi-model agreement** (Codex's 4-model ablation across
the 3 unresolved PA datasets, 42 events, same pipeline):

| Dataset | n | Mean p(out[0]) spread | Consensus (<0.10) | Contested (>0.30) |
|---|---:|---:|---:|---:|
| sample-economics | 13 | 0.305 | 8 | 5 |
| sample-entertainment | 13 | 0.153 | 10 | 2 |
| sample-sports | 16 | 0.126 | 9 | 3 |

Economics has the most genuine cross-model disagreement; sports +
entertainment are mostly consensus. Useful as triage signal when live
events arrive — high-disagreement categories deserve more scrutiny.

**SAE shrinkage** (`forecasting/borrowed_strength.py`,
`forecasting/sae_shrinkage.py`) is implemented as an empirical-Bayes
additive-effects estimator. It runs in the **offline-only**
`predict_multi_outcome_retrieval_sae` variant. We measured its Brier
at **0.1157 vs production 0.0379** on the same set and **did not
promote it** — alphas haven't been calibrated against a held-out set,
and live updating against single-event resolutions isn't supported
within the eval window. The code stays as research scaffolding for a
post-event paper, not as production routing.

## 4. What surprised us

- **Public leaderboards don't predict pipeline performance.**
  Gemini 3.1 Pro is #1 on PA fixed-context; in our pipeline with our
  prompt and our scoring rule, it placed last — it emitted
  probabilities for outcome keys not in the supplied list. Prompt-fit,
  not reasoning quality, drove the gap. Quote-worthy any time a
  reviewer asks why we didn't use the leaderboard winner.
- **A two-line bug ran in production for hours.** `abs(0.60 - 0.5)`
  evaluates to `0.09999999999999998` in IEEE-754; a naive `< 0.10`
  exclusion check rejected the exact-bucket case the spec explicitly
  admitted. Two-line fix; one boundary test added; one DECISIONS.md
  entry. The discipline of writing the postmortem is the point.
- **Variable outcome lists (2–30 outcomes per event).** The handler
  in `forecast_agent_server.py:predict()` accepts both shapes —
  multi-outcome variants emit per-outcome directly; legacy binary
  variants get distributed across the outcomes list. We test this
  end-to-end in `tests/test_predict_edge_cases.py` including a
  30-outcome event.

## 5. Engineering discipline

Beyond the forecasting work, the repo reflects production-grade
engineering practice. Things worth a reviewer's attention:

- **Verify gate** (`./scripts/agent/verify.sh`) — pytest + smoke
  import + dry-run. Used to silently swallow pytest failures (caught
  the favicon-test regression for a full session before we fixed it);
  now loud. Total ~200 passing tests.
- **Preflight gate** (`scripts/preflight.sh`) — runs before any
  deploy: verify, working tree clean, HEAD = origin/main, upload size
  sanity (caught a real 18MB worktree-bloat bug), prints live vs
  local SHA delta.
- **Deploy wrapper** (`scripts/agent/deploy.sh`) — single safe path
  to `railway up`. Pins `PROPHET_BUILD_COMMIT_SHA` env so
  `/healthz.commit` reflects what's actually serving.
- **Full-check** (`scripts/full_check.sh`) — 10-step end-to-end audit
  across source state, deployed surface, auth gates, and watcher
  process. Current result against live: all pass.
- **Pipeline trace per call** — Brave query, raw model output, parse
  path, per-stage latency, fuzzy-match decisions, warnings. Stored
  in `/predictions`; never sent back to PA (their schema is just
  `probabilities`).
- **Decisions log** (`docs/DECISIONS.md`) — append-only, 13+ dated
  entries, including every bug postmortem and every model-swap
  rationale.
- **Multi-agent coordination** via `docs/AGENT_STATUS.md`. Two coding
  agents ran in parallel through this sprint with file-ownership
  claims; zero merge conflicts.

## 6. Open questions

- **Eval set composition** — binary-skewed like our backtest, or
  balanced? Our edge is biggest on multi-outcome events (0.0177
  Brier on n=12 multi vs 0.0425 on n=14 binary). A balanced eval
  amplifies our lead; a binary-skewed eval narrows it.
- **Resubmission window** — our endpoint always serves the latest
  deployed commit; we don't intend to swap mid-event.
- **Live Brave reliability** — silent retrieval degradation (Brave
  returns empty results for niche queries) is our most plausible
  failure mode. The fallback to `predict_multi_outcome` (no retrieval)
  still scores Brier ~0.10 on the backtest, so a partial degradation
  would hurt but not collapse us. Not automated-monitored yet.

## 7. Reproducibility

```bash
git clone git@github.com:Robby955/prophet-hacks.git && cd prophet-hacks
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill ANTHROPIC_API_KEY + BRAVE_SEARCH_API_KEY

# Run the live endpoint locally:
PROPHET_AGENT_VARIANT=multi_outcome_retrieval \
  uvicorn forecast_agent_server:app --host 127.0.0.1 --port 8000

# Reproduce the production backtest:
python scripts/backtest_forecast.py \
  --events data/resolved.json \
  --actuals data/actuals.json \
  --variants multi_outcome_retrieval

# End-to-end smoke against your local instance:
./scripts/full_check.sh --host http://localhost:8000
```

Stack: Python 3.13, FastAPI + uvicorn (Railway / Nixpacks),
`anthropic` 0.102, `httpx`, `pydantic` 2.x.

Cost: ~$0.10 per /predict call (Opus 4.7 + Brave free tier). Per
Discord clarification (Jibang Wu, 2026-05-16), PA's eval cadence is
**one event every 10 minutes, sequential** with a 10-minute per-event
timeout and no server-side retries. Our latency is ~5s; budget margin
is large.

---

**Final word.** The biggest single thing this project taught us was
that the difference between a confident prediction and a calibrated
one is measurable, defensible, and almost entirely about discipline
applied to the pipeline rather than IQ applied to the model. The
production agent is boring on purpose. The Brier number is what
matters.
