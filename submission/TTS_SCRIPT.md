# fish.audio TTS script for the 60-second demo

Voice: **energetic male**. Speed: **default (1.0x)**. Slight pause at every blank line.

Paste the **plain-prose block at the bottom** straight into fish.audio. The labeled
chunks above are just to help you align the audio to your CapCut beats.

Total target: **~145 words = ~58 seconds** at ~150 wpm. Adjust speed in CapCut by
+/-5% if it drifts.

---

## Chunk 1 — Landing (0:00 to 0:08, ~20 words)

> This is The Oracles. A live forecasting endpoint for Prophet Arena. Built
> for Prophet Hacks 2026, Chicago.

## Chunk 2 — Numbers (0:08 to 0:18, ~26 words)

> Brier zero point oh-three-seven-eight on the twenty-six-event backtest.
> Zero point one-two-two-four on the twelve-hundred-event scale set, with
> ninety-five percent confidence interval one-ten to one-thirty-five.

## Chunk 3 — Pipeline (0:18 to 0:35, ~42 words)

> Five stages. Build a Brave Search query. Retrieve five web snippets,
> deduped by domain. Rank them. One forecast call to Claude Opus four-point-seven
> with a market-anchored prompt. Then a Kalshi-paper longshot floor caps
> every outcome at ten percent.

## Chunk 4 — Result (0:35 to 0:48, ~32 words)

> Here is the live demo. The pipeline runs in about three seconds. Every
> stage logs. The rationale, the probabilities, every search hit, every
> model decision, all recoverable on the trace explorer.

## Chunk 5 — Wrap (0:48 to 0:60, ~25 words)

> Calibration overlay. Per-event scatter. Bootstrap confidence intervals.
> Every ablation has a CI, nothing ships unless it clears the gate.
> Repo and report linked. Thanks.

---

## Final plain prose block (paste this verbatim into fish.audio)

```
This is The Oracles. A live forecasting endpoint for Prophet Arena. Built for Prophet Hacks twenty twenty-six, Chicago.

Brier zero point oh three seven eight on the twenty-six event backtest. Zero point one two two four on the twelve hundred event scale set, with ninety-five percent confidence interval one-ten to one-thirty-five.

Five stages. Build a Brave Search query. Retrieve five web snippets, deduped by domain. Rank them. One forecast call to Claude Opus four point seven, with a market-anchored prompt. Then a Kalshi-paper longshot floor caps every outcome at ten percent.

Here is the live demo. The pipeline runs in about three seconds. Every stage logs. The rationale, the probabilities, every search hit, every model decision, all recoverable on the trace explorer.

Calibration overlay. Per-event scatter. Bootstrap confidence intervals. Every ablation has a confidence interval, nothing ships unless it clears the gate. Repo and report linked. Thanks.
```

---

## CapCut alignment notes

Drag the generated audio into CapCut. Cut it at the natural sentence pauses
between the chunks above and slide each chunk under the matching video beat
in `submission/DEMO_SCRIPT.md`:

| Audio chunk | Maps to DEMO_SCRIPT.md beat |
|---|---|
| Chunk 1 | [0:00 - 0:08] Tab A (landing) |
| Chunk 2 | (scroll on the landing showing the numbers card) |
| Chunk 3 | [0:08 - 0:20] Click into Tab B (dashboard) |
| Chunk 4 | [0:20 - 0:45] Click "Run live demo" + watch stages |
| Chunk 5 | [0:45 - 1:00] Show one research page + close |

If a chunk runs long, the next-beat cursor move is forgiving (you can
hold on the previous shot for 1-2 frames more).

## What to avoid in the voice mix

- No background music louder than -18 dB. Speech should be foreground.
- No reverb or "studio" effects. Fish.audio's raw output is fine; CapCut's
  "Voice clarity" preset is fine too. Anything more sounds like a fake AI
  narrator.
- Do not let the volume normalize too aggressively. fish.audio's default
  is already loud enough.
