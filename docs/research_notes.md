# Research Notes — Calibration & Architecture Inputs for The Oracles

Scope: four-source scan to extract concrete changes for `forecast_track.py` /
`forecaster.py` prompts and ensemble logic. Primary metric: Brier on resolved
binary markets.

---

## 2026-05-17 replication note — self-critique is not production-ready

The two-pass "Opus reviews its own first-pass forecast" ablation is unstable
on the 26-event resolved sample. The initial run improved single-binary Brier
from `0.04945` to `0.04652` (delta `-0.00293`, 4 changed events). A fresh
replication at commit `a63d826c` regressed from `0.04945` to `0.05219` (delta
`+0.00274`, 3 changed events).

Conclusion: keep self-critique as an experimental reviewer surface only. Do
not route production forecasts through it unless live PA calls expose the same
failure mode and a measured replicated win appears on comparable event shapes.
The more defensible next experiment is a cheaper in-prompt verification field
(`p_initial`, `verification`, final probability) rather than a second model
call after the first forecast.

---

## 1. arxiv 2503.01307 — "Cognitive Behaviors that Enable Self-Improving Reasoners" (Gandhi et al., 2025)

Source: <https://arxiv.org/abs/2503.01307>

The paper isolates four behaviors that separate self-improving models from
ones that plateau under RL on Countdown: **verification, backtracking,
subgoal setting, backward chaining**. Headline (Sec. 4–5): traces that
*exhibit* these behaviors improve the model even when *incorrect*.

**Concrete prompt change** for our `SYSTEM_PROMPT` in `forecaster.py` lines
101–119: replace the freeform "rationale" with a structured four-step trace
that mirrors the paper's four behaviors. This costs ~50 extra completion
tokens but should reduce overconfident point-estimates.

```diff
- - Output ONLY valid JSON: {"p_yes": <float>, "rationale": "<string>"}
+ - Output ONLY valid JSON:
+   {"subgoals": ["<resolution criterion>", "<what would force YES>", "<what would force NO>"],
+    "backward_check": "<work back from resolution date: what must be true?>",
+    "p_initial": <float>,
+    "verification": "<is p_initial consistent with base rate and time-to-resolution?>",
+    "p_yes": <float>,
+    "rationale": "<1-2 sentences>"}
+ - If verification flags inconsistency, p_yes must move toward 0.5 vs p_initial.
```

The "backward_check + verification" step is the cheapest behavioral
modification with a clear Brier-score story: it forces an explicit
self-critique pass before the number is committed.

---

## 2. Astral Codex Ten — "Shameless Guesses, Not Hallucinations" (Scott Alexander)

Source: <https://www.astralcodexten.com/p/shameless-guesses-not-hallucinations>

Alexander's claim: LLMs are trained on next-token prediction with no penalty
for guessing, so they emit confident answers when the calibrated response is
"I don't know." He doesn't prescribe a fix, but the framing maps directly to
our `p_yes` floor/ceiling.

**Concrete prompt change** — already partially present (we clamp to
[0.01, 0.99] and tell the model "prefer 0.50 over fake precision"). The
missing piece: an **explicit abstain channel that maps to 0.5, not to a
refusal**. Add to `_build_user_prompt`:

```diff
+ parts.append(
+   "\nABSTAIN RULE: If you have no informational edge over the market "
+   "mid-price, set p_yes within 0.02 of the mid. Do not invent signal. "
+   "Outputting near-mid is the correct answer when uncertain — it is not "
+   "a failure."
+ )
```

This converts the "shameless guess" failure mode (model anchoring on a
plausible-sounding integer like 0.65) into an explicit pull-toward-market
default, which is Brier-optimal under no-edge conditions. Pair with a
post-hoc shrinkage step in `forecast_track.py`: `p_final = 0.85 * p_model +
0.15 * p_mid` when the model's rationale length is below a threshold (proxy
for low conviction).

---

## 3. E-values (Vovk & Wang, *Annals of Statistics* 2021)

Source: <https://arxiv.org/abs/1912.06116>

Tangential. Brier on single-shot probabilities is a proper-scoring-rule
problem, not a hypothesis test. E-values would only help on the *meta*
question "is forecaster A better calibrated than B across the live
session?" because they merge by averaging without alpha-spending — out of
scope for this submission.

---

## 4. PolyStrat (Olas) and TimeCopilot

PolyStrat source: <https://olas.network/blog/introducing-polystrat-an-autonomous-ai-prediction-agent-on-polymarket>
TimeCopilot source: <https://github.com/TimeCopilot/timecopilot> and <https://arxiv.org/abs/2509.00616>

PolyStrat is an FSM harness (read news → weigh → size → submit) with two
strategies: "balanced" (fixed size, ignores confidence) and "risky" (size
scales with confidence + odds). Borrow the **fixed-size balanced mode as
a control arm** in `risk.py` — we currently size by edge, so adding it
gives a clean A/B and a safety net if the LLM is miscalibrated.

TimeCopilot orchestrates an LLM over 30+ time-series foundation models
(Chronos, Moirai, TimesFM, TimeGPT) with auto-selection and CV. Most of
our markets are event-binary, but for markets with a clean numeric
anchor ("S&P closes above X on date Y", "hurricane wind > Z"), route to
TimeGPT as a third member in `forecaster.py:_calibrated_ensemble`. Gate
on `market.topic in {"markets", "weather", "economy"}` so we don't spend
budget on political markets where TSFMs have no signal.

---

## Summary of concrete changes (prioritized)

1. **Structured CoT JSON** with backward_check + verification step in `SYSTEM_PROMPT` (from arxiv 2503.01307). Highest expected Brier impact.
2. **Explicit abstain-to-mid rule** in user prompt + low-conviction shrinkage in `forecast_track.py` (from ACX).
3. **Fixed-size control arm** in `risk.py` (from PolyStrat) for A/B safety.
4. **TimeGPT side-channel** for numeric/time-anchored markets only (from TimeCopilot). Optional; only if API budget permits.

E-values: nothing actionable.
