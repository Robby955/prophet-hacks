# ForecastingPath Observatory Design

## Status

Design spec for the next dashboard iteration. This branch does not modify
production routes, `static/`, `submission/`, `docs/DECISIONS.md`,
`docs/FINDINGS.md`, `chat_completions_adapter.py`, or Railway variables.

Chosen direction: **Hybrid Observatory**. The first screen is operational
enough to watch the system, but the deeper tabs explain the research work,
ablation state, scoring caveats, and per-call trace behavior.

## Goals

1. Give Rob a single internal page that answers: what is live, what has
   happened, what did the model see, what did it output, and what might be
   wrong.
2. Make model and prompt decisions defensible under adversarial review.
   Every claim needs provenance: prediction file, script, commit, or live
   endpoint.
3. Separate public portfolio content from competition-sensitive operational
   detail during the active event.
4. Avoid new paid actions in the first implementation. The first version reads
   existing artifacts and live health only.

## Non-Goals

- Do not change `PROPHET_AGENT_VARIANT`.
- Do not promote GPT-5.5, Opus 4.6, SAE, Groq, or any prompt variant without a
  measured win and Rob's explicit approval.
- Do not expose raw predictions, raw LLM outputs, prompt variants, or detailed
  failure analysis on the public landing page during the active event.
- Do not duplicate the existing `/dashboard` implementation until PR #5
  (`codex/sse-demo`) settles.

## Public Visibility Audit

Current public page is technically impressive but reveals too much for an
active competitive window:

- Exact model: `claude-opus-4-7`.
- Exact production variant: `multi_outcome_retrieval`.
- Exact retrieval stack: Brave Search, top 5, dedupe rules.
- Exact post-processing formula:
  `min(0.10, max(0.05, 0.5 / n_outcomes))`.
- Commit SHA.
- Benchmark table, failure modes, model comparison details.
- Links toward source and decision logs.

Recommended split:

| Surface | Audience | Content |
|---|---|---|
| Public landing | Judges, reviewers, casual visitors | Project name, high-level method, health, sign-in link, concise credibility claims. No raw recipe. |
| PIN observatory | Rob and trusted collaborators | Exact model, variant, traces, ablations, latency, prompt/retrieval experiments, failure modes. |
| Repo/docs | Post-event portfolio and reviewers | Full methodology, decisions, reproducibility, paper draft after scoring correction. |

Public landing should say enough to be credible but not enough to give away the
playbook while live scoring is still active.

## GPT-5.5 Answer

GPT-5.5 was tried. It should not replace production.

After the scoring-methodology correction on current `main`, the relevant table
is:

| Variant | Single-binary Brier | Proper multi-class | Multi-only |
|---|---:|---:|---:|
| Opus 4.7 production | 0.0378 | 0.2558 | 0.4551 |
| Opus 4.6 | 0.0391 | 0.2500 | 0.4396 |
| GPT-5.2 | 0.0438 | 0.2874 | 0.4971 |
| GPT-5.5 | 0.0920 | 0.3429 | 0.6552 |

The earlier "GPT-5.5 binary 0.0376" fact is true only for the binary subset,
not for the full endpoint metric. The production decision should use the
metric PA's local CLI evaluator actually computes: single-binary Brier over all
26 events. On that metric GPT-5.5 is worse than Opus 4.7, Opus 4.6, and
GPT-5.2.

Short answer for collaborators:

> We did try GPT-5.5. It has the best binary-only score on the subset, but its
> all-event endpoint Brier is 0.0920 vs Opus 4.7 at 0.0378, and its
> multi-outcome diagnostics are weak. It is useful as an ablation, not a
> production swap.

## Observatory Layout

### Overview Tab

Purpose: one glance tells whether anything is broken.

Cards:

- Live health from `/healthz`: status, commit, variant.
- PA call count and last call time from `/predictions` when authenticated.
- Brave health from the latest `scripts/brave_health.sh` artifact or live
  probe if wired later.
- Deployment drift: latest main SHA vs `/healthz.commit`.
- Open risk banner: "waiting for first PA call", "first call received",
  "latency high", "Brave degraded", "scoring available".

Main chart:

- Timeline of PA calls, one row per prediction.
- Latency waterfall summary when traces exist: Brave, LLM, parse, total.

### Live Calls Tab

Purpose: inspect what happened on actual PA traffic.

Table columns:

- Time.
- Event title and category.
- Outcome count.
- Returned `p_yes` and top probabilities.
- Parse path.
- Fuzzy match count.
- Warnings.
- Total latency.

Detail drawer:

- Evidence snippets and domains.
- Raw LLM output.
- Parsed probability map.
- Longshot guard before/after if available.
- Exact JSON returned to PA.

### Experiments Tab

Purpose: track what was actually tested and what remains untested.

Sections:

- Model ablation: Opus 4.7, Opus 4.6, GPT-5.2, GPT-5.5, Gemini.
- Prompt ablation: production, no anchor, no calibration scale, minimal,
  meta-role prompt.
- Retrieval-count ablation: 0, 3, 5, 8 evidence chunks.
- Scoring mode: single-binary PA CLI vs proper multi-class.
- Bootstrap/decomposition: floor fix vs model swap.

Each row should show:

- Status: not run, running, done, invalidated, superseded.
- Dataset.
- Cost.
- Commit or script.
- Primary metric.
- Notes and caveats.

### Adversarial Review Tab

Purpose: prepare excellent answers before reviewers ask.

Questions to answer directly:

- Why not GPT-5.5?
- Why not Gemini?
- Why use Opus 4.7 if Opus 4.6 has slightly better proper multi-class score?
- Is the 26-event backtest contaminated by resolved web evidence?
- Why is retrieval count fixed at 5?
- What happens if Brave fails?
- What happens if PA sends events without `outcomes`?
- What is the scoring mismatch between PA docs and PA CLI?
- What is the biggest known weakness?
- What would you change after first live PA call?

Every answer should link to an artifact or script, not just prose.

### Public Page Gate

Purpose: decide what belongs on `/`.

Public content should be one of:

- High-level architecture.
- Health and sign-in.
- Honest result summary after metric correction.
- Source link only if the repo is meant to be public during the event.

Public content should not include:

- Raw prompts.
- Exact prompt variants.
- Full failure-mode autopsies.
- Raw predictions or traces.
- Detailed exploit-like recipe for improving against our current method.

## Backend Fit

No new database is required for version 1.

Inputs already exist:

- `/healthz` for live commit and variant.
- `/predictions` for authenticated recent calls and traces.
- `data/predictions/*.json` for model ablations.
- `reports/phase2_vs_phase1_backtest.json` for decomposition.
- `static/summary.html` and report assets for plots.
- `scripts/brave_health.sh` for retrieval dependency health.

Recommended v1 implementation:

- Generate a static JSON artifact such as `static/observatory.json` from
  existing files.
- Serve an auth-gated `/observatory` page from `forecast_agent_server.py`
  or a committed static page behind the existing login.
- Keep paid actions out of v1.

Recommended v2 implementation:

- Add live SSE updates after PR #5 is merged or closed.
- Add "run synthetic demo" only with rate limiting and clear spend copy.
- Add stored live predictions outside process memory if Railway restarts
  become a real observability problem.

## Design Principles

- Dense but calm. This is an operator/research surface, not a landing page.
- Every number has provenance visible nearby.
- Do not mix scoring metrics. Single-binary and proper multi-class must be
  labeled everywhere.
- Treat "not run" as a first-class state. It is better than implying a result.
- Keep public and private surfaces separate.
- Avoid model leaderboard arguments without in-pipeline evidence.

## Acceptance Criteria

Version 1 is done when:

- `/observatory` or equivalent auth-gated page shows live health, model
  ablation state, prompt/retrieval experiment state, and public/private
  visibility warnings.
- It renders without paid API calls.
- It makes the GPT-5.5 answer unambiguous.
- It flags the stale workshop-paper metric table until rewritten.
- Tests cover any new builder/parsing helpers.
- `./scripts/agent/verify.sh` passes.

## Notes for Other Agents

- Main is currently beyond my earlier PR #10. Commit `94da03d` appears to have
  superseded `codex/summary-metric-fix`; close or ignore PR #10 after verifying
  no unique test coverage is needed.
- Do not edit `docs/FINDINGS.md`, `submission/`, or `docs/DECISIONS.md` from
  this design branch.
- `docs/WORKSHOP_PAPER_DRAFT.md` still contains stale pre-correction claims
  about GPT-5.5 and the multi-outcome gap. That should be fixed by whoever owns
  the paper/content lane.
- Public `/` should be softened during active scoring. Move exact recipe and
  failure-mode details into the PIN-protected observatory.
