# Final state + roadmap - Sun 2026-05-17, pre-submit

Public repository note: this is a historical pre-submit handoff. The
dashboard PIN is redacted from this copy and should be shared privately
only when reviewer access is required.

Everything below is freeze-frame as of commit `3221215` deployed to
`agent.forecastingpath.com`. Read this top-to-bottom before you start the
recording.

## What is live right now

- **Endpoint**: `POST https://agent.forecastingpath.com/predict` —
  registered with Prophet Arena as team `CanadaHacks`, project
  `The Oracles`, variant `multi_outcome_retrieval`.
- **Public landing**: `https://forecastingpath.com/`
- **Health**: `/healthz` and `/health` both 200, both return current
  commit SHA.
- **SSE streaming demo**: `/dashboard?record=1` + Run live demo button.
  Tested end-to-end: 5 stages fire over ~3-6s, Brave ~200ms, Opus 4.7
  ~2.7-3s, real probabilities + rationale come back.
- **10 research pages public** for judges (no auth needed):
  `/static/{gallery_resolved,gallery_open,summary,scatter_resolved,heatmap_resolved,calibration_overlay,abstain_slider,bootstrap_hist,pipeline_trace,variance}.html`
  Plus `/compare` and `/compare-open`.
- **Operator-only (PIN-gated)**: `/dashboard`, `/observatory`, `/review`,
  `/predictions`, `/events`, `/demo/start` (costs ~$0.05/run),
  `/static/status.html`.

## Honest assessment

**Strengths:**
1. The submission is *measured*. Every claim has a script that recomputes
   it (`scripts/bootstrap_brier_ci.py`, `scripts/check_retrieval_leakage.py`,
   `scripts/analyze_subset_1200.py`).
2. The Subset-1200 scale validation (0.1224 vs 0.0378) is the kind of
   honest framing most submissions skip. Judges who notice will weight
   it heavily.
3. The bug-fix dominance finding (longshot floor at 0.25 → 0.10) is a
   real engineering story, not a model-selection story. Easy to verify
   from `docs/DECISIONS.md`.
4. Methodology discipline: two of our own candidate changes were rejected
   on a bootstrap CI bar and we logged the rejection. That's hard to fake
   and easy for a reviewer to verify.
5. The 75-second video script is tight, the recording-mode UI is clean,
   and the demo actually works live.

**Weaknesses to be aware of:**
1. **No live PA score yet.** The endpoint went live today; the 2-week PA
   scoring window starts now. The 0.0378 and 0.1224 are backtest only.
   Don't overstate. (We don't in the writeup.)
2. **Small n on the headline backtest (n=26).** The Subset-1200 number is
   the credible expected magnitude. Both are reported, but a hostile
   reviewer could nitpick.
3. **Demo video has to be re-recorded.** The automated Playwright capture
   is unusable. Plan + scripts are ready, you have to actually do it.

**Chances read (subjective):**

- Forecasting-track shortlist: realistic. The submission has visible
  engineering discipline (preflight gate, /healthz with SHA, deploy
  scripts, 280 tests, append-only decisions log), measured ablations
  with CIs, and a live demo. That's not common at a 36-hour hackathon.
- Overall placement: depends heavily on how PA scores the live calls.
  We're set up for whatever the first PA call shape is (parser
  hardened, schema safety nets, longshot floor sane).
- Portfolio value (independent of placement): high. The repo + research
  pages + report are strong solo-engineer artifacts to point to in
  technical interviews.

## What you still have to do, in order

### 1. Fix `www.forecastingpath.com` Vercel (5 min)

- Vercel → TheoremPath project → Settings → Domains → remove
  `www.forecastingpath.com`.
- Optional: Railway oracles-agent → Settings → Domains → add
  `www.forecastingpath.com` (Vercel will give you a CNAME to put in
  your DNS). Either way, the Devpost copy uses only the apex.

### 2. Record the 60-90 sec video (15 min including retakes)

Open these tabs in order **after signing in once**
(`https://forecastingpath.com/login?next=/dashboard?record=1`, private PIN):

| Tab | URL | Time on screen |
|---|---|---|
| 1 | `https://forecastingpath.com/` | 0:00–0:05 |
| 2 | `https://forecastingpath.com/dashboard?record=1` | 0:05–0:40 |
| 3 | `https://agent.forecastingpath.com/static/heatmap_resolved.html` | 0:40–0:50 |
| 4 | `https://agent.forecastingpath.com/static/gallery_resolved.html` | 0:50–1:05 |
| 5 | `https://agent.forecastingpath.com/static/abstain_slider.html` | 1:05–1:15 |

Click cue for Tab 2: pre-select **Super Bowl LXI** preset, then **Run
live demo** at 0:10. The pipeline takes ~3-6 seconds; the TTS narration
covers the wait.

Full beat-by-beat: `submission/DEMO_SCRIPT.md`.

### 3. Generate the TTS audio (5 min)

- Open fish.audio (you already chose energetic male voice).
- Paste the **single plain-prose block** at the bottom of
  `submission/TTS_SCRIPT.md` verbatim.
- Generate, download mp3.
- Drop into CapCut on the audio track, align chunk boundaries to the
  five video beats. The CapCut alignment table is in TTS_SCRIPT.md.

### 4. Flip GitHub repo public (1 click)

- `Robby955/prophet-hacks` → Settings → Change visibility → Public.
- Confirm the README renders inline with the architecture diagram.
- Verify the LICENSE shows Apache 2.0.
- Verify the 0.0378 / 0.1224 numbers + bootstrap CI are visible in the
  README "Results" section.

You **don't** need to clean the commit history for the Devpost
submission. The portfolio cleanup (a fresh repo with one curated initial
commit) is planned for post-event — see `submission/PUBLIC_REPO_PLAN.md`.

### 5. Fill Devpost form + submit

- Open `https://devpost.com/submit-to/29074-ai-forecasting-hackathon/manage/submissions/1020007-the-oracles/project_details/edit`.
- Each form field maps to a labeled block in `submission/DEVPOST_PASTE.md`.
- Upload the video.
- Submit.

## Frequently asked, answered

**Q: Why is /dashboard still PIN-gated if we're making the repo public?**

The PIN is **not** code secrecy. The repo flipping public exposes the
*code* (which is supposed to be open). The PIN protects the live
`/demo/start` endpoint, which costs ~$0.05 per click in Brave + Opus
API spend. Without auth, anyone with the URL could spam the button and
drain budget. The PIN is friction, not a secret.

Same for `/observatory` and `/predictions`: those expose the live
prediction trace store, and during the 2-week active PA scoring
window we keep them private as a precaution. Post-event, both can open
up. See `submission/PUBLIC_REPO_PLAN.md` for the showcase-repo plan
which drops the auth path entirely.

**Q: Can we keep deploying improvements during the PA scoring window?**

Yes. Anri (PA org) confirmed in Discord today: "you can update it as
you'd like. We won't be verifying that the code stays the same or
anything." Constraint: mid-window deploys mix pre- and post-change
scoring, so we tag commits with SHA + UTC time so the post-event
retrospective can attribute Brier deltas correctly. The bootstrap-CI
promotion bar still applies: |delta| > 0.01 single-binary Brier with
95% CI excluding zero on the offline backtest before deploy. See
`docs/DECISIONS.md` 2026-05-17 entry.

**Q: Did we actually build the moodspan-style streaming pipeline?**

Yes. Tested live today: `/demo/start?preset=fed` returns a `run_id`,
then `/demo/stream/{run_id}` is an SSE endpoint that fires five events
(queued → build_event → forecast running → forecast completed →
completed) over ~3-6 seconds with real Brave + Opus latencies in the
payload. Visible on `/dashboard?record=1` with the moodspan-style UI:
preset cards, stage timeline, output panel.

**Q: What if the live demo fails on camera?**

Three fallbacks in DEMO_SCRIPT.md. Short version: Brave timeout =
narrate the graceful degrade, demo hang = cut to result page (TTS
covers the airtime), cookie expired = stop the take, re-auth off-camera,
restart.

## Files Rob should know about

- `submission/DEMO_SCRIPT.md` — video beats
- `submission/TTS_SCRIPT.md` — fish.audio prose + CapCut alignment
- `submission/DEVPOST_PASTE.md` — Devpost form copy-paste
- `submission/PROJECT_STORY.md` — Devpost "Inspiration / What I built /
  Challenges / Built with" detailed copy
- `submission/REPORT.md` — long-form technical writeup
- `submission/PUBLIC_REPO_PLAN.md` — post-event showcase-repo plan
- `submission/FINAL_STATE.md` — this file
- `docs/DECISIONS.md` — append-only methodology log
- `README.md` — top-of-repo, public when you flip the repo

## Deploy / live ops one-liners

```bash
# health check + live SHA
curl -s https://agent.forecastingpath.com/healthz

# verify /predict against a synthetic event
curl -s -X POST https://agent.forecastingpath.com/predict \
  -H "Content-Type: application/json" \
  -d '{"event_ticker":"smoke","market_ticker":"smoke","title":"will the Fed cut rates in December 2026?","description":"binary","outcomes":["Yes","No"],"close_time":"2026-12-15T00:00:00Z"}' \
  | head -c 400

# deploy + auto-poll for SHA flip
./scripts/agent/deploy.sh "<one-line msg>"

# fresh PIN if rotated
railway variables --service oracles-agent --environment production --kv | grep DASHBOARD
```
