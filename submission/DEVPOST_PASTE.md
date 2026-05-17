# Devpost form · copy-paste-ready

One file, every Devpost field already filled in, in submission order.
Paste each block into its labeled form field at
https://prophethacks.devpost.com/submissions

The PIN for any judge wanting to see the operator console is
**176661**, but they do not need it — every load-bearing artifact
(report, architecture, source, live endpoint) is publicly reachable.

---

## Project name

```
The Oracles
```

## Tagline (≤200 chars)

```
A calibrated retrieval-augmented forecasting agent for Prophet Arena. Opus 4.7 + Brave Search + a Kalshi-paper longshot floor, with measured bootstrap CI and an honest leakage audit.
```

## Elevator pitch (≤256 chars)

```
Live forecasting endpoint for Prophet Arena. Mean Brier 0.0378 single-binary on the 26-event resolved backtest (0.0377 +- 0.0009 across 5 reruns), 40.8% reduction over the Sonnet baseline, 95% paired-bootstrap CI [0.0143, 0.0374].
```

## What it does (long description)

```
The Oracles is a probabilistic forecasting agent built for Prophet Arena's forecasting track. It answers POSTed event webhooks with calibrated per-outcome probabilities, backed by recent web evidence and anchored to any cited market odds.

Five observable pipeline stages run for every event:

1. Build a Brave Search query from the event title and most informative outcome.
2. Retrieve five web-evidence snippets, deduped by domain with .gov / .edu / exchanges of record prioritized.
3. Rank and cap the chunks.
4. Single Anthropic Claude Opus 4.7 forecast call. The system prompt enforces a 0.50 to 0.90 calibration scale and explicit market-odds anchoring: if the evidence cites an implied probability, anchor to it and move more than 5pp only with specific contrary signal.
5. Kalshi longshot floor: every per-outcome probability floored at min(0.10, max(0.05, 0.5/n)), then renormalized. The 0.10 cap is the empirical Kalshi threshold below which buyers historically lose more than 60%.

Each call writes a full trace to disk: Brave query, raw model output, parse path, per-stage latency, fuzzy outcome-label matches, and warnings. None of those fields are returned to Prophet Arena; they live in the auth-gated /observatory dashboard.

The submission is grounded in three findings and two negative results.

Findings:
- Bug-fix dominance. About 85% of the headline Brier improvement over the Sonnet baseline came from a one-line fix to the post-LLM longshot floor formula, not from a model upgrade. The old max(0.05, 0.5/n) evaluated to 0.25 for binary events, silently clamping every binary prediction into [0.25, 0.75] regardless of model output.
- Scoring rule matters more than the model. Prophet Arena's CLI evaluator scores single-binary Brier; their published docs describe proper multi-class Brier; their actual live scoring is a Brier skill score against snapshotted Kalshi/Polymarket prices. The three rules rank our model lineup differently on n=26.
- Schema discipline beats raw capability for prompt-strict contracts of this shape. On the same retrieval and prompt and post-processing, GPT-5.5 and Gemini 3.1 Pro Preview score 18 to 46 times worse than Opus 4.7 on multi-outcome events. The failure mode is JSON-schema noncompliance on outcome labels, not a reasoning gap.

Negative results worth recording:
- Adversarial-review prompts regress on a calibrated production model. Two independent variants (two-call self-critique, one-call verification field) both pull confident-and-correct predictions toward the middle, costing Brier where production was right to be confident.
- Small ablations need a confidence-interval bar. Two intuitive candidate changes (adaptive retrieval count, exchanges-only source priority) failed the paired-bootstrap promotion gate at alpha=0.05 on n=26. They did not ship.

Every published claim is backed by an on-disk artifact: prediction JSON, bootstrap-CI script, leakage-audit script, variance ablation. The audit script flags 38.5 percent of resolved events as having at least one post-resolution URL in the evidence list, so the 0.0378 number is best-case-with-hindsight and we say so prominently in the report.

The auth-gated research console at agent.forecastingpath.com/observatory shows live prediction traces, a 5-model side-by-side gallery with per-event drill-down, a cross-model heatmap, an interactive abstain-policy slider that visualizes Prophet Arena's actual scoring rule, a bootstrap distribution histogram, an intra-model variance plot, and a step-by-step pipeline trace explorer for educational replay. The public root shows a sparse product page.

We coordinated two AI agents in parallel via docs/AGENT_STATUS.md with explicit file-ownership claims. Zero merge conflicts. Methodology lessons live in docs/DECISIONS.md (append-only, 20+ dated entries) so any future iteration can see what was tried and rejected before being tried again.
```

## How I built it (Devpost "How we built it")

```
Python 3.13. FastAPI plus uvicorn on Railway with Nixpacks builds. Anthropic Claude Opus 4.7 as the forecasting model, with Sonnet 4.6 and Haiku 4.5 as fallbacks. Brave Search Web API for retrieval. OpenRouter unified API for the multi-vendor ablation harness (tested Opus 4.6, GPT-5.2, GPT-5.5, and Gemini 3.1 Pro Preview through the same pipeline).

Engineering scaffolding:
- scripts/preflight.sh: gate-and-print check before every deploy. Verify green, working tree clean, HEAD = origin/main, upload size sanity (caught a real 18MB worktree-bloat bug), prints live vs local SHA delta.
- scripts/agent/deploy.sh: single safe path to railway up. Pins commit SHA into PROPHET_BUILD_COMMIT_SHA env so /healthz.commit reflects what is actually serving.
- scripts/full_check.sh: 10-step end-to-end audit across source state, deployed surface, auth gates, and watcher process.
- scripts/bootstrap_brier_ci.py: paired-bootstrap confidence interval for any two prediction files.
- scripts/check_retrieval_leakage.py: word-boundaried regex audit of evidence URLs for post-resolution markers.
- scripts/ablate_variance.py: 5-run intra-model variance estimate.

Frontend is hand-written HTML plus a small amount of Plotly via CDN for the interactive views. No build step, no framework. Auth-gated static research pages live at /static/ behind a PIN-checked middleware; the public root and /healthz and /static/summary.pdf and /static/architecture.svg are open.

Testing: 270+ pytest cases. The verify gate (scripts/agent/verify.sh) was tightened mid-build after we discovered it had been silently swallowing pytest failures (now loud). The submission report's public copy quality is itself a test (tests/test_public_text_quality.py bans non-ASCII em-dashes, marketing speak, and any number known to be stale).
```

## Challenges I ran into

```
- A silent production bug. The Kalshi longshot floor formula returned 0.25 for binary events instead of the intended 0.10, silently clamping every binary prediction into [0.25, 0.75] regardless of LLM output. Found by a smoke test on a synthetic Chiefs and Super-Bowl-LXI event after an unrelated model swap. About 85 percent of the eventual headline improvement came from this one-line fix; the model swap accounted for the remaining 15 percent.

- A methodology bug in the ablation harness. backtest_forecast.py dropped the per-outcome probabilities array from each saved prediction, keeping only p_yes. The summary-report generator then defaulted to a uniform 1/n distribution for multi-class scoring, producing apparent 25-times multi-outcome gaps that turned out to be metric-mixing across files. Fixed; postmortem in docs/DECISIONS.md.

- Three Railway deploys failed silently with TLS BadRecordMac errors before we noticed .claude/worktrees/ was uploading 18MB of agent state on every push. Fix: .gitignore the worktrees plus preflight du -sh check.

- The scoring rule was not what we thought. Prophet Arena's CLI scores single-binary Brier; their published docs describe proper multi-class; their actual live scoring (confirmed mid-build in Discord) is a Brier skill score against snapshotted market prices. Three different metrics, three different model rankings on n=26. The right adjustment was to report all three and resist promoting any change on a metric the actual evaluator does not implement.

- Adversarial-review prompts looked promising on a single run and failed to replicate. A two-call self-critique pattern improved Brier by 0.003 in run 1, regressed by 0.003 in run 2. A one-call verification-field pattern regressed by 0.019. Both pull confident-and-correct predictions toward the middle. Recorded as negative results.
```

## Accomplishments I am proud of

```
- Three load-bearing findings and two honest negative results, each backed by an on-disk artifact a reviewer can rerun.
- A methodology bar (paired-bootstrap CI, magnitude > 0.01 single-binary Brier on n=26) that I rejected two of my own candidate production changes against, in writing, in docs/DECISIONS.md.
- A backtest leakage audit that I ran on myself and reported prominently. Most submissions would hide a 38.5 percent post-resolution-URL rate; we surface it in the top section of static/summary.html and explain why cross-model rankings are still leakage-invariant.
- An intra-model variance estimate (5 reruns, sigma = 0.0009) that turns "production scores 0.0378" into "production scores 0.0377 +- 0.0009 across 5 reruns" and justifies the noise-floor cutoff retroactively.
- A clean separation of public and private surfaces. The public root and /healthz and /static/summary.pdf are open. Operator details, raw traces, and ablation pages live behind a PIN during active scoring.
```

## What I learned

```
- Calibration discipline beats most prompt tuning. Two adversarial-review patterns regressed; the same Brier improvement came from a one-line floor fix.
- Pin the scoring rule to the exact evaluator. Selecting a model on a metric the grader does not implement is the same mistake as training-test split contamination.
- Retrieval over resolved events leaks. Word-boundaried search markers (won, winner, champion, final) flag a third of our events. Chronological-replay benchmarks (FutureSim, Goel et al. 2026) are the right substrate for the next iteration.
- A 0.01 single-binary Brier delta is the minimum signal to clear noise on n=26. Anything smaller is run-to-run LLM stochasticity. Two of our own candidate changes failed this bar and did not ship.
- Two AI coding agents can cooperate on one repo if file-ownership claims are explicit and the decision log is append-only. We had zero merge conflicts across roughly 80 commits.
```

## What is next for The Oracles

```
- Run our agent through FutureSim's three-month chronological-replay benchmark as a non-leaking substrate.
- Try a confidence-aware critique: only revise low-confidence initial predictions, leaving confident-and-correct ones alone. Open hypothesis after the two regression results.
- Test market-aware blending. If Prophet Arena's actual live payload includes a market price for the outcome, blend it with the model probability conditional on retrieval confidence.
- Validate the schema-discipline finding on a larger non-leaking sample. n=26 with 16 sports matchups is directional, not conclusive.
```

## Built with

```
python, fastapi, uvicorn, pydantic, anthropic, claude-opus-4-7, brave-search-api, openrouter, railway, cloudflare, plotly, pytest, numpy
```

## Try it out / links

```
Live forecasting endpoint:  https://agent.forecastingpath.com/predict
Public landing:             https://forecastingpath.com/
Submission report (PDF):    https://forecastingpath.com/static/summary.pdf
Architecture diagram:       https://forecastingpath.com/static/architecture.svg
GitHub repository:          https://github.com/Robby955/prophet-hacks
Health endpoint:            https://forecastingpath.com/healthz
Auth-gated research views:  https://agent.forecastingpath.com/observatory (PIN: 176661)
```

## Demo video

Upload `forecastingpath-walkthrough.webm` from Codex's overnight capture
(under `output/playwright/overnight/` or wherever Codex saved it).
If asked for a hosted URL instead of a file upload, host on YouTube or
Loom and paste the link.

---

## Last-mile checks before clicking submit

- [ ] Repo flipped to public (GitHub Settings -> Change visibility -> Public)
- [ ] README renders the architecture.svg inline (visible at top of GitHub view)
- [ ] LICENSE shows Apache 2.0
- [ ] All public links in this file return 200 in incognito
- [ ] Demo video uploaded
- [ ] All form fields filled with the blocks above

Hit submit.
