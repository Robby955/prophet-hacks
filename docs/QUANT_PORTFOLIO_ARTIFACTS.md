# Quant portfolio artifacts

This repo should remain useful even if the final Prophet Arena score is
weak. The durable artifact is not just a leaderboard rank. It is the
record of a measured forecasting system: endpoint deployment, calibrated
probability work, negative ablations, trace capture, operations, and a
plain retrospective.

The standard is simple: preserve what happened, what was measured, what
failed, and what a future reviewer can reproduce.

Rule: even if the final Prophet Arena score is weak, keep the evidence
clean enough for a reviewer to understand the work.

---

## 1. What to keep before making the repo public

Do this after the event closes and before flipping repository visibility.

- **Submission artifacts:** `submission/REPORT.md`,
  `submission/PROJECT_STORY.md`, and `docs/SUBMISSION.md`.
- **Research artifacts:** `docs/FINDINGS.md`,
  `docs/WORKSHOP_PAPER_DRAFT.md`, and `docs/RESEARCH_QUEUE.md`.
- **Decision record:** `docs/DECISIONS.md`, including rejected
  experiments and bug postmortems. Do not rewrite history to make the
  path look cleaner than it was.
- **Operations record:** `docs/RUNBOOK.md`, `docs/LIVE_OPERATIONS.md`,
  `scripts/full_check.sh`, `scripts/preflight.sh`, and
  `scripts/agent/deploy.sh`.
- **Evaluation inputs and outputs:** `data/resolved.json`,
  `data/actuals.json`, `data/predictions/*.json`, and the scripts under
  `scripts/` that generated Brier, bootstrap, and ablation results.
- **Demo assets:** `docs/DEMO_CAPTURE_GUIDE.md`,
  `scripts/capture_demo_assets.sh`, and captured files under
  `output/playwright/` when they do not expose secrets.
- **Live traces:** prediction history from `/predictions` if it is useful
  and safe to preserve. Redact raw provider output, private tokens, and
  anything that could identify non-public event payloads if required.

## 2. Portfolio claims that survive a poor score

These are valid even if the live score disappoints, as long as the files
above remain accurate.

- A deployed FastAPI forecasting endpoint served Prophet Arena's required
  `probabilities` schema.
- The system recorded per-call pipeline traces: retrieval query, evidence,
  raw model output, parse path, latency, warnings, and fallback behavior.
- Evaluation used proper scoring rules rather than anecdotal examples:
  Brier, Brier Skill Score, ECE, Murphy decomposition, and paired
  bootstrap checks.
- Several candidate improvements were rejected because confidence
  intervals crossed zero. This is a strength, not a weakness.
- A real boundary bug was found and fixed: binary longshot floors were
  previously too aggressive for two-outcome events.
- The repo has repeatable gates: tests, smoke imports, dry-run agent
  execution, preflight deploy checks, and live health verification.
- The project documents uncertainty. It separates measured production
  changes from experimental prompts, single-seed results, and ideas that
  need live data.

## 3. Claims to avoid until live evidence supports them

Do not use these claims in public writeups unless the post-event data
backs them up.

- "market-beating" or "alpha" without a live Team Brier versus Market
  Brier comparison.
- "Best model" claims based only on the 26-event sample-resolved set.
- General claims about GPT-5.5, Gemini, Opus, or Sonnet outside this
  specific pipeline, prompt, schema, and dataset.
- Any statement that retrieval improved live performance unless the
  live traces show retrieval coverage and the scored events support it.
- Any claim that the offline backtest is leakage-free unless the evidence
  timestamps have been audited against event resolution times.

## 4. If live score disappoints

Document the failure mode directly. Useful failure explanations include:

- Prophet Arena sent a payload shape or outcome list that differed from
  the sample data.
- Brave returned thin, stale, or post-resolution evidence.
- Market baselines were stronger than model forecasts on the live event
  mix.
- The offline resolved-event backtest was inflated by search leakage.
- The agent overfit to binary events while the live set had a different
  outcome distribution, or the reverse.
- The model produced valid JSON but poor calibration.
- Latency, deploy state, auth, or routing problems affected one or more
  calls.
- The live sample was too small for a stable conclusion.

The retrospective should state which of these happened and cite the trace,
commit, or script output that proves it.

## 5. Post-event checklist

1. Run the post-event orchestrator once actuals are available.

```bash
./scripts/post_event_orchestrator.sh --actuals <path-to-actuals>
```

2. Run the full operational audit.

```bash
./scripts/full_check.sh
```

3. Fill `docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md` with the final live
   numbers.
4. Copy any useful screenshots or video captures into `output/playwright/`
   and note which route and commit produced them.
5. Update `README.md` and `submission/REPORT.md` only with claims that
   the retrospective can defend.
6. Do one final secret scan before public release.

## 6. Reviewer framing

The strongest version of the project is a documented forecasting
experiment, not a polished story with the hard parts removed.

A good reviewer should be able to answer:

- What did the agent predict?
- What did the market imply?
- What evidence did it retrieve?
- How did the parser handle the model output?
- What was the latency?
- Which changes improved measured Brier and which did not?
- What would Rob change with another week?

If the repo answers those questions, it remains a serious portfolio piece
regardless of final rank.
