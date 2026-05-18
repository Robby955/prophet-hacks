# Demo narration script - 75 seconds, 5 beats

**Voice**: energetic male. **Speed**: default 1.0×. **Pause** at every
blank line.

**Total target**: ~185 words → ~74 seconds at ~150 wpm.

Paste the **single plain-prose block** at the bottom into the narration tool.
The labeled chunks are for CapCut alignment to `submission/DEMO_SCRIPT.md`.

---

## Chunk 1 — Public root (0:00–0:05, ~13 words)

> The Oracles. A live forecasting endpoint for Prophet Arena, built for
> Prophet Hacks twenty twenty-six.

## Chunk 2 — Live pipeline demo (0:05–0:40, ~80 words)

> The centerpiece is a live retrieval-augmented pipeline. Each event
> Prophet Arena posts goes through five stages.
>
> A Brave Search query is built from the title. Five web snippets are
> retrieved, deduped by domain, ranked. One forecast call to Claude Opus
> four point seven, with a market-anchored prompt. A Kalshi-paper
> longshot floor caps every outcome at ten percent.
>
> Three seconds end to end. Probabilities, rationale, evidence URLs,
> every search hit, every latency — all logged on disk per call.

## Chunk 3 — Cross-model heatmap (0:40–0:50, ~25 words)

> Cross-model agreement on the resolved backtest. Same prompt, same
> retrieval, six models scored. Opus four point seven dominates the
> multi-outcome events.

## Chunk 4 — Resolved gallery (0:50–1:05, ~32 words)

> Per-event drill-down. Twenty-six resolved events, every model's
> probability, every Brier score. Brier zero point oh three seven eight
> on this set, zero point one two two four on the twelve hundred event
> scale set.

## Chunk 5 — Abstain slider (1:05–1:15, ~28 words)

> Strategic insight. Predict only when you have edge, otherwise defer
> to the market price. Every ablation has a bootstrap confidence interval.
> Nothing ships unless it clears the gate. Thanks.

---

## Single plain-prose block

```
The Oracles. A live forecasting endpoint for Prophet Arena, built for Prophet Hacks twenty twenty-six.

The centerpiece is a live retrieval-augmented pipeline. Each event Prophet Arena posts goes through five stages.

A Brave Search query is built from the title. Five web snippets are retrieved, deduped by domain, ranked. One forecast call to Claude Opus four point seven, with a market-anchored prompt. A Kalshi-paper longshot floor caps every outcome at ten percent.

Three seconds end to end. Probabilities, rationale, evidence URLs, every search hit, every latency, all logged on disk per call.

Cross-model agreement on the resolved backtest. Same prompt, same retrieval, six models scored. Opus four point seven dominates the multi-outcome events.

Per-event drill-down. Twenty-six resolved events, every model's probability, every Brier score. Brier zero point oh three seven eight on this set. Zero point one two two four on the twelve hundred event scale set.

Strategic insight. Predict only when you have edge, otherwise defer to the market price. Every ablation has a bootstrap confidence interval. Nothing ships unless it clears the gate. Thanks.
```

---

## CapCut alignment cheat-sheet

| Chunk | Audio time | Maps to DEMO_SCRIPT.md beat | Action in CapCut |
|---|---|---|---|
| 1 | 0:00–0:05 | Beat 1 (Public root, Tab 1) | Land audio start at video frame 1 |
| 2 | 0:05–0:40 | Beat 2 (Dashboard?record=1, Run live demo) | The narration covers the ~3-6s server wait; pad timeline if your local demo finishes faster |
| 3 | 0:40–0:50 | Beat 3 (heatmap_resolved.html) | Tab switch ⌘3 lands at 0:40 |
| 4 | 0:50–1:05 | Beat 4 (gallery_resolved.html) | Tab switch ⌘4 lands at 0:50 |
| 5 | 1:05–1:15 | Beat 5 (abstain_slider.html) | Tab switch ⌘5 lands at 1:05 |

## CapCut audio mix

- Voice: -12 dB to -6 dB peak (CapCut auto-normalize is fine)
- No background music. If you must, -24 dB ducked under voice.
- No reverb and no "studio voice" effect. Keep the narration natural.
- Voice clarity filter: ON (CapCut default).

## What to drop if you run long

If the cut runs to 80+ seconds, the safest cut is the second half of
Chunk 4 (the scale-set sentence). Chunks 1, 2, and 5 are load-bearing.
Chunk 3 can also be shortened to one sentence ("Cross-model agreement
on the resolved backtest, same prompt, six models.").
