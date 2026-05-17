# fish.audio TTS script V2 — matched to Rob's recorded 2:13 video

Replaces the 75-second V1. Beats match Rob's actual recorded layout:

| Time | Visual on screen |
|---|---|
| 0:00 – 0:06 | Public landing (forecastingpath.com) |
| 0:06 – 0:21 | Fed rate cut demo (66% No / 34% Yes result) |
| 0:21 – 0:24 | Transition (silent) |
| 0:24 – 0:35 | Per-event gallery — green/red Brier bars by category |
| 0:36 – 0:49 | Cross-model heatmap — Opus 4.7 column green |
| 0:49 – 1:09 | Abstain threshold slider |
| 1:09 – 1:29 | Super Bowl LXI demo (5-outcome) |
| 1:29 – 1:50 | UK PM demo (3-outcome) |
| 1:50 – 2:08 | Resolved-event scatter plot |
| 2:08 – 2:13 | Brief summary / close |

Voice: **energetic male**. Speed: **1.0×**. fish.audio default.

---

## Chunk 1 — Public landing (0:00–0:06, ~15 words)

> The Oracles. A live forecasting endpoint for Prophet Arena, built for
> Prophet Hacks twenty twenty-six.

## Chunk 2 — Fed rate cut demo (0:06–0:21, ~37 words)

> Here it runs in real time. Will the Fed cut rates in December
> twenty twenty-six? Five stages. Brave Search retrieves recent
> evidence. Opus four point seven reads it and assigns thirty-four
> percent yes, sixty-six percent no.

## Chunk 3 — Per-event Brier gallery (0:24–0:35, ~27 words)

> Per-event Brier scores across six models on twenty-six resolved
> events. Green is calibrated, red is wrong with confidence. Opus
> four point seven holds the line on multi-outcome questions.

## Chunk 4 — Cross-model heatmap (0:36–0:49, ~32 words)

> Cross-model agreement. Same retrieval, same prompt, six models
> scored. Opus four point seven dominates because of schema discipline,
> not raw reasoning. The losers emit probability mass on labels not
> in the outcome list.

## Chunk 5 — Abstain slider (0:49–1:09, ~50 words)

> Strategic insight. Predict only when you have edge. The slider here
> moves the abstention threshold. As the cutoff rises, the system
> defers more outcomes to the market price. The shipping policy is
> calibrated to the noise floor of n equals twenty-six, with a
> bootstrap confidence interval excluding zero before any change
> deploys.

## Chunk 6 — Super Bowl LXI demo (1:09–1:29, ~50 words)

> Five-outcome event. The Kalshi-paper longshot floor caps every
> outcome at ten percent, so unlikely teams never go to zero. Buyers
> of contracts priced under ten cents on Kalshi lose more than sixty
> percent on average. Floor plus renormalize is the discipline that
> turns LLM overconfidence into a fair prior.

## Chunk 7 — UK PM demo (1:29–1:50, ~52 words)

> Three-outcome politics question. Who is Prime Minister of the
> United Kingdom on January first, twenty twenty-seven. The pipeline
> handles arbitrary outcome counts. Brave fetches recent
> betting-market chatter and news. The model anchors to any cited
> market odds and moves more than five points only with specific
> contrary signal.

## Chunk 8 — Resolved-event scatter (1:50–2:08, ~45 words)

> Every dot is one resolved event. Predicted probability against
> actual outcome. The line is perfect calibration. We cluster near
> it on confident-and-correct, drift on confident-and-wrong. The
> backtest scored zero point oh three seven eight Brier. At twelve
> hundred event scale, zero point one two two four.

## Chunk 9 — Close (2:08–2:13, ~12 words)

> Two AI agents coordinated through one repo over the weekend. Repo
> and report linked. Thanks.

---

## Single plain-prose block (paste this verbatim into fish.audio)

```
The Oracles. A live forecasting endpoint for Prophet Arena, built for Prophet Hacks twenty twenty-six.

Here it runs in real time. Will the Fed cut rates in December twenty twenty-six? Five stages. Brave Search retrieves recent evidence. Opus four point seven reads it and assigns thirty-four percent yes, sixty-six percent no.

Per-event Brier scores across six models on twenty-six resolved events. Green is calibrated, red is wrong with confidence. Opus four point seven holds the line on multi-outcome questions.

Cross-model agreement. Same retrieval, same prompt, six models scored. Opus four point seven dominates because of schema discipline, not raw reasoning. The losers emit probability mass on labels not in the outcome list.

Strategic insight. Predict only when you have edge. The slider here moves the abstention threshold. As the cutoff rises, the system defers more outcomes to the market price. The shipping policy is calibrated to the noise floor of n equals twenty-six, with a bootstrap confidence interval excluding zero before any change deploys.

Five-outcome event. The Kalshi-paper longshot floor caps every outcome at ten percent, so unlikely teams never go to zero. Buyers of contracts priced under ten cents on Kalshi lose more than sixty percent on average. Floor plus renormalize is the discipline that turns LLM overconfidence into a fair prior.

Three-outcome politics question. Who is Prime Minister of the United Kingdom on January first, twenty twenty-seven. The pipeline handles arbitrary outcome counts. Brave fetches recent betting-market chatter and news. The model anchors to any cited market odds and moves more than five points only with specific contrary signal.

Every dot is one resolved event. Predicted probability against actual outcome. The line is perfect calibration. We cluster near it on confident-and-correct, drift on confident-and-wrong. The backtest scored zero point oh three seven eight Brier. At twelve hundred event scale, zero point one two two four.

Two AI agents coordinated through one repo over the weekend. Repo and report linked. Thanks.
```

---

## If you want to cut the abstain-slider beat (~20 seconds)

Two options:

**Option A**: Drop Chunk 5 entirely, slide everything after it earlier
by 20 seconds. Video becomes 1:53. Re-time the visuals: cut the abstain
slider footage from CapCut, slide Super Bowl / UK PM / scatter earlier.

**Option B**: Keep the slider footage but replace Chunk 5 with a shorter
narration over it:

> Strategic insight. Predict only when you have edge. The slider moves
> the abstention threshold. Every ablation has a bootstrap confidence
> interval, nothing ships unless it clears the gate.

That's 30 words, ~12 seconds. The remaining 8 seconds of abstain footage
stays under that audio.

## CapCut alignment

Drop the generated mp3 onto the audio track. Use these markers:

- **0:00**: Chunk 1 start
- **0:06**: Chunk 2 start (Fed)
- **0:21**: ~3 second pause (transition silence)
- **0:24**: Chunk 3 start (gallery)
- **0:36**: Chunk 4 start (heatmap)
- **0:49**: Chunk 5 start (abstain)
- **1:09**: Chunk 6 start (Super Bowl)
- **1:29**: Chunk 7 start (UK PM)
- **1:50**: Chunk 8 start (scatter)
- **2:08**: Chunk 9 start (close)

If fish.audio's pacing produces a slightly different total length, you
have two levers: CapCut "Speed" on the audio track (0.95×–1.05×), or
trim silence between chunks. Don't pitch-shift.

## Audio mix

- Voice peak: -6 dB to -12 dB
- No music. If you must, ducked to -24 dB under voice.
- No reverb, no "studio" filter. fish.audio's raw output reads as a
  real person.
- CapCut "Voice clarity" preset is fine. Anything more sounds AI.
