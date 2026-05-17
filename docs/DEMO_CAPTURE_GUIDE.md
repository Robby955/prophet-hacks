# Demo Capture Guide

Use this for the Devpost/video pass or for still screenshots. The goal is to
show the system as an operating forecasting desk, not as a code walkthrough.

## Principles

- Do not show tokens, PIN entry, `.env`, `variables.txt`, Railway variables,
  or browser autofill.
- Stay on the public root or an already-authenticated browser session.
- Prefer the live deployed host unless you are recording a risky edit in
  progress. The live host currently proves deploy, auth, and routing.
- Keep the story visual: run loop, live dashboard, trace explorer, strategy
  slider, event map.

Computer Use is optional. Terminal and Playwright are enough for repeatable
screenshots. Use Computer Use only if you need to operate QuickTime, Chrome,
or another local screen-recording app; Rob should perform the final upload or
Devpost submission himself.

## Sixty-second screencast

| Time | Screen | What to show |
|---:|---|---|
| 0-8s | `https://forecastingpath.com/` | Brand, endpoint status, run-loop preview |
| 8-22s | `/dashboard` | Health, commit, first-call state, private research links |
| 22-35s | `/dashboard` pipeline demo | Click "Run pipeline demo"; show streamed stages and returned JSON |
| 35-45s | `/static/pipeline_trace.html` | One resolved event replayed stage by stage |
| 45-53s | `/static/abstain_slider.html` | Move the threshold slider; show score changing |
| 53-60s | `/review` or `/static/summary.html` | One line: measured Brier, CI, and what did not ship |

If the demo run is slow, cut from the queued state to the result JSON after it
finishes; do not narrate latency unless asked.

## Still screenshots

Capture these in order:

1. Public root, desktop `1440x900`.
2. Public root, mobile `390x844`.
3. Dashboard first-call block.
4. Pipeline demo result JSON after one run.
5. Pipeline trace explorer.
6. Abstain slider with a nonzero threshold.
7. Per-event scatter.
8. Bootstrap histogram.

Suggested filenames under ignored `reports/demo/`:

```text
01-public-root-desktop.png
02-public-root-mobile.png
03-dashboard-first-call.png
04-dashboard-demo-result.png
05-pipeline-trace.png
06-abstain-slider.png
07-event-scatter.png
08-bootstrap-hist.png
```

## Repeatable screenshot commands

Fast path:

```bash
scripts/capture_demo_assets.sh
```

This starts a local server with an empty prediction store, dashboard auth
unset, provider keys unset, and the production variant name pinned. It writes
screenshots and `forecastingpath-walkthrough.webm` under
`output/playwright/forecast-demo/` including desktop and mobile public-root
shots. It does not call `/predict` or
`/demo/start`.

Manual path, if you want to drive the browser yourself: use the existing
Playwright CLI wrapper. The output directory is ignored by git.

```bash
mkdir -p reports/demo
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
export PWCLI="$CODEX_HOME/skills/playwright/scripts/playwright_cli.sh"

"$PWCLI" -s=forecast-demo open https://forecastingpath.com/
"$PWCLI" -s=forecast-demo resize 1440 900
"$PWCLI" -s=forecast-demo screenshot

"$PWCLI" -s=forecast-demo resize 390 844
"$PWCLI" -s=forecast-demo screenshot
```

For authenticated captures, sign in once in the browser and avoid recording
the PIN field. Then navigate to the private views from the page links.

## One-page PDF

Build a compact shareable PDF from `submission/REPORT.md`:

```bash
python scripts/build_submission_onepager.py
```

Output: `output/pdf/oracles-submission-onepager.pdf`. The script reads the
submission report but does not modify files under `submission/`.

## Things to be careful about when talking through the demo

- The 0.0378 and 0.1224 numbers are backtest scores. There is no live PA
  score yet; the endpoint just went live. Say so.
- The retrieval-count sweep and source-priority swap are research, not
  shipping changes. The CIs crossed zero on n=26, so they're recorded in
  `docs/DECISIONS.md` and not in production.
- GPT-5.5 and Gemini 3.1 Pro Preview lost on this exact pipeline (this
  prompt, this parser, this floor). They aren't bad models, they just
  don't fit this contract well. That's the actual finding.
- The public landing is intentionally sparse. The research views
  (galleries, scatter, calibration, bootstrap, pipeline trace) are linked
  from the dashboard nav. /observatory and /review stay PIN-gated.
