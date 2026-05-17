# 60-second demo screen-record script — The Oracles

Use this to record a single take. Browser at 1280x720 or larger. Voice
overlay in plain conversational tone. No effects. Target length 55-65
seconds; faster is fine.

## Pre-flight (off-camera, do once)

1. Two browser tabs already loaded:
   - **Tab A**: <https://forecastingpath.com/> (public landing)
   - **Tab B**: <https://agent.forecastingpath.com/dashboard> (live pipeline demo)
2. On Tab B, scroll to the "Pipeline console" section so the "Run live
   demo" button is visible. Do NOT click it yet.
3. Pick **one** preset card from the three (Fed rate cut / UK PM /
   Super Bowl LXI) — recommended: **Fed rate cut** because it's
   the 2-outcome case and the explanation is the cleanest.

## Beats (read out loud while screen-recording)

**[0:00 - 0:08] Tab A — the landing.**
"This is The Oracles, our forecasting-track submission for Prophet Hacks.
A retrieval-augmented Claude Opus 4.7 endpoint that Prophet Arena hits live."

(Scroll once to surface the result card showing Brier 0.0378 single-binary,
0.1224 on Subset-1200.)

"Two numbers matter: 0.0378 on our 26-event backtest, 0.1224 on the
1200-event scale-up. The smaller one is hindsight-rich. The larger one
is what we expect to roughly match live PA."

**[0:08 - 0:20] Click into Tab B (dashboard).**
"This is the live operator console. The pipeline runs in five stages —
query, retrieve, rank, forecast, floor. Each stage logs."

(Hover the preset cards.) "Pick a synthetic event — I'll run the Fed one."

**[0:20 - 0:45] Click "Run live demo".**
"This is a real call to the production endpoint. Brave Search hits five
sources, Opus 4.7 reads them with the anchored calibration prompt, and
the Kalshi-paper longshot floor caps every outcome at 10%."

(Watch the stage timeline tick through.)

"There's the rationale. There's the per-outcome probabilities. And the
full trace — every search hit, every model decision — is recoverable on
the prediction-trace explorer."

**[0:45 - 1:00] Briefly open one research page (Tab B → top nav → "Event
map" or jump to `/static/scatter_resolved.html`).**

"The research views are public for judging — calibration diagram,
per-event scatter, bootstrap CI, ablations across six models. Every
ablation delta has a paired-bootstrap CI; nothing ships unless it clears
the gate. Repo and report linked from the landing. Thanks."

## What NOT to do

- Don't show /predictions or /observatory — those have prediction
  payloads in them that may include any first PA call.
- Don't show /static/status.html — it's still PIN-gated and the 401 page
  is ugly.
- Don't open the Devpost form on-camera.
- Don't claim numbers that aren't on screen. The 40.8% relative number is
  in the landing card; the rest is for the report.

## Quick alternates if the live demo fails on-camera

- If Brave times out: the demo gracefully falls back to no-retrieval.
  Narrate: "and here it gracefully degrades when retrieval is missing —
  same prompt, no evidence." Continue.
- If the page errors entirely: switch to `/static/gallery_resolved.html`
  (per-event drill-down across five models). It's static evidence of the
  same story.
- If `/healthz` is showing a stale SHA: skip the dashboard beat and walk
  through `/static/summary.html` instead.

## After recording

- Trim head and tail to remove the cursor settling.
- Export as 1080p mp4 or webm, under 50 MB.
- Upload to Devpost form video field at
  <https://devpost.com/submit-to/29074-ai-forecasting-hackathon/manage/submissions/1020007-the-oracles/project_details/edit>.
