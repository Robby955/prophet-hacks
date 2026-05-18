# 60-90 second demo screen-record script — The Oracles

**Use this order.** Five beats, target 75 seconds total. The TTS script at
`submission/TTS_SCRIPT.md` is timed to
match exactly these beats.

Browser at **1440×900**. macOS Screen Recording or QuickTime → File →
New Screen Recording → "Selected Portion" → drag a 1440×900 selection
over the browser. Do not stitch screenshots.

## Pre-flight (off-camera, do once)

1. Sign in once at `https://forecastingpath.com/login?next=/dashboard?record=1`
   with the private dashboard PIN. Cookie persists. Do NOT record this step.
2. Open these five tabs in this order, all already loaded:
   - **Tab 1**: `https://forecastingpath.com/` (public landing)
   - **Tab 2**: `https://forecastingpath.com/dashboard?record=1` (live demo, recording layout)
   - **Tab 3**: `https://agent.forecastingpath.com/static/heatmap_resolved.html`
   - **Tab 4**: `https://agent.forecastingpath.com/static/gallery_resolved.html`
   - **Tab 5**: `https://agent.forecastingpath.com/static/abstain_slider.html`
3. On Tab 2, pre-select the **Super Bowl LXI** preset card (the 5-outcome
   one). It produces better multi-outcome visuals than Fed.
4. Hit ⌘L to clear the URL bar focus before you start recording.

## Beats (with exact click cues)

### Beat 1 — Public root [0:00 – 0:05]

Tab 1 already visible. Hold for 4 seconds. Brand, status pill, the two
result numbers (0.0378 / 0.1224) should be in the viewport.

> No clicks. Pan eyes from top headline to numbers card.

### Beat 2 — Live pipeline demo [0:05 – 0:40]

Switch to Tab 2 (⌘2). Recording-mode layout shows ONLY the demo panel.

| Sub-beat | Time | Action |
|---|---|---|
| 2a | 0:05–0:10 | Hold on the demo panel. Super Bowl LXI card is highlighted. |
| 2b | 0:10–0:12 | Click **Run live demo**. |
| 2c | 0:12–0:35 | Hold. Stage timeline ticks: Queue → Event → Retrieve → Forecast → Return. Output panel fills with probabilities + rationale. |
| 2d | 0:35–0:40 | Pan eyes over the JSON output. |

The demo takes about 3–6 seconds wall-clock on the server. The chat
script keeps narrating during the wait so dead air doesn't hurt.

### Beat 3 — Cross-model heatmap [0:40 – 0:50]

Switch to Tab 3 (⌘3). Hold.

> No clicks. Hover one cell briefly if you want a tooltip moment.

### Beat 4 — Resolved gallery [0:50 – 1:05]

Switch to Tab 4 (⌘4). Scroll down once slowly (1 trackpad swipe).
Click on any row to expand a per-event drill-down. Hold 2 seconds. Click
to collapse.

### Beat 5 — Abstain slider [1:05 – 1:15]

Switch to Tab 5 (⌘5). Drag the threshold slider from the left to the
right once, slowly. The "predictions kept" count and score drop visibly.

## End [1:15]

Stop recording. CapCut trim head+tail.

## Quick alternates if something fails on camera

- If the live demo hangs >10s: cut to a still of the result page; the TTS
  script's beat 2 narration covers ~30s so you have airtime.
- If Brave times out: the system falls back to no-retrieval. The probabilities
  are still returned; just narrate "and here it gracefully degrades without
  evidence." Continue.
- If the cookie expired: you'll get redirected to /login. Stop the take,
  re-auth in another tab, do not re-record the auth screen.

## What NOT to do on camera

- Don't show `/observatory`, `/predictions`, `/static/status.html`,
  `/review` — operator-only or PIN-gated.
- Don't show the PIN entry screen.
- Don't show `~/Desktop/variables.txt` or any terminal with API keys.
- Don't reference the parallel-agent coordination (it's in the writeup,
  not the demo).
- Don't open the Devpost form on camera.

## After recording

- CapCut: trim head+tail to clean cursor settles.
- Drop the fish.audio mp3 from `submission/TTS_SCRIPT.md` onto the audio
  track. Align the chunk boundaries to the beats above.
- Export 1080p mp4, under 50MB.
- Upload to Devpost form video field.
