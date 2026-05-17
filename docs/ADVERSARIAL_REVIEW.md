# Adversarial review · 2026-05-17 03:00 CT

Self-review of the project's writing, research, and methodology as if a
hostile reviewer was hunting for issues. Done before flipping the repo
public, so anything embarrassing or wrong gets fixed before judges see
it. Author: Claude. Sleeping author: Rob.

## Methodology: defensible

| Claim | Where | Verdict |
|---|---|---|
| Production Brier = 0.0378 single-binary | submission/REPORT.md, README | **Defensible.** Reproduces from `data/predictions/multi_outcome_retrieval.json` to 6 decimals (0.037819). PA's CLI evaluator confirms. |
| Production Brier = 0.0377 +- 0.0009 with variance | static/summary.html, docs/DECISIONS.md | **Defensible.** Five fresh reruns sit in `data/predictions/variance_run_*.json`. Grand mean 0.03772, std 0.00090, range [0.03613, 0.03822]. Canonical sits inside +-1 sigma. |
| 40.8% relative reduction over Sonnet baseline | README, WORKSHOP_PAPER, REPORT | **Defensible.** (0.0639 - 0.0378) / 0.0639 = 40.814%. |
| 95% CI [0.0143, 0.0374] on Phase 2 delta | README, REPORT, WORKSHOP_PAPER | **Defensible + reproducible.** Codex's `scripts/check_bootstrap_seed_stability.py` confirmed CI is stable to ~0.0001 across seeds 20260516/20260517/20260518. |
| ~85% of headline win from floor bug fix | REPORT, README, FINDINGS, WORKSHOP_PAPER | **Defensible.** (0.0639 - 0.0418) / (0.0639 - 0.0378) = 84.7%, rounds to ~85%. Sonnet+new-floor=0.0418 is from a Codex rerun in `multi_outcome_retrieval.phase1_sonnet.json`. |
| Murphy decomposition values | summary.html, FINDINGS | **Defensible.** Sanity check: REL - RES + UNC equals direct Brier within rounding error (max residual 0.0018 for Gemini). |
| 38.5% events have post-resolution URLs | DECISIONS, summary.html, FINDINGS | **Defensible.** `data/predictions/leakage_audit.json` lists 10 of 26 events with explicit examples (Variety/ESPN URLs containing "winner"/"champion"). Word-boundaried regex, not loose substring. |
| Adversarial-review prompts regress | summary.html, FINDINGS, WORKSHOP_PAPER | **Defensible.** Two independent runs: self-critique (replication +0.00274) and one-call verification (+0.01871). Two independent failure modes. |

## Inconsistencies found and fixed in this review

| Where | What | Fix |
|---|---|---|
| README.md line 12 vs 126 | Same doc said both "40.8% reduction" and "40.7% relative" | Now both 40.8% with explicit arithmetic shown |
| README.md line 129 | Used `±` (non-ASCII, fails Codex's `test_public_text_avoids_non_ascii_punctuation`) | Replaced with "about" |

## Vulnerabilities a hostile reviewer could attack

These are honest weaknesses we acknowledge in the submission. We do
not hide them; the submission report calls them out.

### 1. n=26 is small and binary-skewed (16/26 sports)

Already disclosed in REPORT section 3, WORKSHOP_PAPER section 4.5,
FINDINGS section "Sample size honesty," and summary.html. A reviewer
could push: "your bootstrap CI is on the same 26 events you optimized
against." True, and that's why we promote no findings without bootstrap
CI excluding zero + |delta| > 0.01 (the noise-floor rule, in
`docs/DECISIONS.md` 2026-05-17). The Phase 2 win cleared that bar; E3
and E4 did not, so they did not ship.

### 2. Backtest retrieval leakage on resolved events

Disclosed prominently in summary.html top section ("Backtest leakage
disclosure"), DECISIONS 2026-05-16, WORKSHOP_PAPER section 3.7. 38.5%
of events retrieve post-resolution URLs. The 0.0378 number is
best-case-with-hindsight. We say so explicitly in the headline view.
Cross-model rankings remain leakage-invariant (every variant shares
retrieval), so the schema-discipline finding survives.

### 3. PA's actual scoring rule is BSS vs market, which we cannot
   verify offline

Disclosed in summary.html top section, WORKSHOP_PAPER section 4.1,
DECISIONS 2026-05-16. The 0.0378 verifies against PA's CLI but not
against the live market-Brier rule the organizers described in
Discord. We say so. The market-odds-anchoring prompt block is in
production because under the live rule it is *protective* when the
market has signal we lack; we did not delete it on offline ablation
evidence alone.

### 4. The Opus 4.7 vs Opus 4.6 production choice is metric-dependent

Single-binary favors 4.7 (0.0378 vs 0.0391); multi-class favors 4.6
(0.2500 vs 0.2558). On n=26 the gap is inside the noise floor. We
hold 4.7 because PA's CLI scores single-binary and the Sonnet -> 4.7
transition is the production-tested path. This is disclosed in REPORT
section 3 and WORKSHOP_PAPER section 4.1. A reviewer could legitimately
ask why not 4.6; the answer is documented.

### 5. Schema-compliance hypothesis is qualified, not absolute

Earlier drafts overclaimed a "25x multi-outcome gap." The corrected
version (REPORT, FINDINGS, WORKSHOP_PAPER) says the strongest defensible
claim is "Gemini and GPT-5.5 fail JSON outcome-label compliance; the
top-three cluster competes within noise." Codex's
`tests/test_public_text_quality.py` bans the old "18-46" framing so it
cannot creep back in.

### 6. SAE shrinkage variant exists but does not ship

`forecasting/sae_shrinkage.py` + `forecasting/borrowed_strength.py` are
checked in, exposed via `predict_multi_outcome_retrieval_sae`, but
scored 0.1157 vs production 0.0378 on the same set. Disclosed as a
negative result in REPORT, FINDINGS, WORKSHOP_PAPER. A reviewer asking
"why is this code here?" gets a clear answer: research scaffolding for
a post-event paper, deliberately not promoted.

## Areas the reviewer could press harder and where to point them

| Pressure point | Pre-built answer | Artifact |
|---|---|---|
| "Show me the per-event noise" | static/variance.html | `scripts/ablate_variance.py` + `data/predictions/variance_run_*.json` |
| "Show me bootstrap CI stability" | `output/research/bootstrap_seed_stability_*.json` | `scripts/check_bootstrap_seed_stability.py` (3 seeds, spread <0.001) |
| "Show me which events leaked" | leakage_audit.json lists top suspects per ticker | `scripts/check_retrieval_leakage.py` |
| "Show me adversarial-review failures" | self_critique.json and verification_prompt.json | scripts in `scripts/ablate_*` |
| "How do I reproduce the headline" | `./run.sh backtest` | run.sh, deterministic seed pinned |
| "Where is your test discipline" | tests/test_public_text_quality.py and tests/test_static_research_pages.py | 263+ tests passing |

## Things we deliberately do not claim

- We do not claim production is SOTA on Prophet Arena.
- We do not claim Brier 0.0378 is what we will get on live PA events.
- We do not claim the schema-compliance finding generalizes beyond our
  pipeline + prompt.
- We do not claim the adversarial-review regression is universal across
  prompt patterns; we only show two failures of two patterns we tried.
- We do not claim multi-class Brier is the right metric, only that
  PA's docs describe it and their CLI does not implement it.

Each of these would be a stronger story if claimed but would also be
indefensible under questioning. We chose the smaller true version.

## Recommendations to consider before the 17:00 CT submit

1. **Read the abstract + introduction of WORKSHOP_PAPER once** to
   confirm the framing is in your voice. Currently bias-checked but
   you are the author of record.
2. **Skim REPORT.md sections 1-3** for the same reason. Codex and I
   both polished it; final tone is your call.
3. **Look at summary.pdf one more time.** It is the primary public
   artifact and the version judges will skim.
4. **Decide on PR #12** (the open production-touching PR from the
   parallel review agent). We held it for review; recommend not merging
   without the market-Brier re-measurement gate.
5. **Flip repo public 30 minutes before submitting.** GitHub Settings ->
   Change visibility -> Public.

## What is not in this review

- Source code style or architecture concerns (not adversarial-judge
  surface; judges read the report and dashboard).
- Internal coordination docs (AGENT_STATUS, CODEX_GOALS, CLAUDE.md):
  these are honest project artifacts and may be a positive signal for
  an AI hackathon judge.
- Test coverage gaps (263+ tests, no measured holes).
- DEMO_CAPTURE_GUIDE.md (Codex owns this).

## Sign-off

The submission is defensible to a hostile reviewer if you read it
the way we wrote it: as a small-sample empirical study with explicit
caveats, three findings backed by reproducible artifacts, two negative
results worth recording, and a methodology bar that rejected its own
candidate improvements. The credible artifact is not the headline
number; it is the discipline applied to it.

Decided by: Claude. Goodnight.
