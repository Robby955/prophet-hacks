# Roadmap — Prophet Hacks 2026 · Team CanadaHacks · Project The Oracles

*Updated 2026-05-16 ~10:05 CT. Append-only at the bottom; revise inline above.*

## TL;DR

**Submission target:** Prophet Hacks **forecasting track**, scored on Brier against resolved events. Lower is better.

**Where we are right now:**
- Pipeline: `forecast_track.py` → `prophet forecast predict` → `prophet forecast evaluate`. End-to-end working against the 26-event `sample-resolved` backtest set.
- Best Brier so far: **0.189** with single Claude Sonnet 4.6 call per event. vs 0.219 uniform prior, 0.250 random.
- Team registered as `CanadaHacks` via the API. Project name `The Oracles` for the Devpost form.
- Live `prophet forecast events --status open` returns `No open events found` — hackathon-day events not yet posted as of 10:00 CT.

**Critical path to a real submission:**
1. Prophet Arena posts live events (gated on them, not us).
2. We register an **HTTP endpoint URL** with `prophet forecast register --endpoint-url ...` so the server can call our agent when events arrive — OR we discover a manual upload path via the CLI / API.
3. We run our best variant on the live events and the server scores us once the events resolve.

Everything else is iteration on **Brier quality** of the predict function.

---

## Timeline (CT)

| Time | Phase | What happens |
|---|---|---|
| Sat 09:00 | Kickoff | Event opens; PA_SERVER_API_KEY available via `prophetarena.co/profile/api-keys`. |
| Sat 09:00–11:00 | Setup + first Brier | venv, deps, team registered, 5-variant backtest on `sample-resolved`. ✅ done |
| Sat 11:00–14:00 | Model frontier sweep | Add Opus 4.6, GPT-5.2, ensemble variants. Pick winner on backtest. |
| Sat 14:00–18:00 | Endpoint hosting | Decide submission mechanism (HTTP endpoint vs file upload). Stand up the endpoint if needed (FastAPI + ngrok / Vercel / Railway). |
| Sat 18:00–24:00 | Live forecasts | Run agent over whatever live events Prophet Arena posts; rinse and repeat each tick. |
| Sun 00:00–09:00 | Iterate / retrieval | Web-search-augmented variant for high-uncertainty events. Sleep block somewhere in here. |
| Sun 09:00–17:00 | Final tuning + submit | Lock in best variant, write final Project Story, submit to Devpost. |
| Sun 17:00 | Hard deadline | Submission window closes. |

Cells marked with ✅ are done. The rest is a **non-binding plan** — revise as we learn.

---

## Variants — what we have and what we're testing

| ID | Variant | Model(s) | Status | Brier (n=26) |
|---|---|---|---|---|
| V0 | `uniform_prior` | deterministic `1/len(outcomes)` | ✅ shipped | 0.219 |
| V1 | `single_llm` | Claude Sonnet 4.6 | ✅ shipped | **0.189** |
| V2 | `opus_47` | Claude Opus 4.7 | ⏳ rerunning | TBD |
| V3 | `gpt55` | OpenAI GPT-5.5 | ⏳ rerunning | TBD |
| V4 | `ensemble_logit` | Sonnet 4.6 + GPT-5.5, logit-mean | ⏳ rerunning | TBD |
| V5 | `opus_46` | Claude Opus 4.6 (top leaderboard agent) | not started | — |
| V6 | `gpt52` | OpenAI GPT-5.2 (top leaderboard fixed-context OAI) | not started | — |
| V7 | `gemini3` | Google Gemini 3 Pro (top leaderboard fixed-context overall) | blocked: no API key | — |
| V8 | `ensemble_3way` | Opus 4.6 + GPT-5.2 + Sonnet 4.6 | not started | — |
| V9 | `retrieval_aug` | V5 + web search per uncertain event | not started | — |
| V10 | `self_consistency_3` | V1 run 3× per event, median p_yes | not started | — |

**Rule of thumb:** ship the variant that wins on backtest with margin > 1×SE of paired bootstrap. Don't ship the marginally-better one if it costs 5× more compute.

---

## Tasks — owner, success criterion, eta

| Task | Owner | Success | ETA | Status |
|---|---|---|---|---|
| Sync `.env` from `variables.txt` | Claude | `prophet forecast register` succeeds | done | ✅ |
| Register `CanadaHacks` team | Claude | API returns "Team registered" | done | ✅ |
| Build `sample-resolved` backtest | Claude | one number per variant in `data/predictions/backtest_summary.json` | done | ✅ |
| Add Opus 4.6 + GPT-5.2 variants | Claude | both pass smoke; appear in `VARIANTS` dict | 15 min | next |
| Pick winning variant on backtest | Claude | paired bootstrap on Brier deltas, n=26 | 30 min | post-rerun |
| Decide submission mechanism | Rob + Claude | confirmed: HTTP endpoint vs file upload | 20 min | needs Discord ask |
| Stand up HTTP endpoint (if needed) | Claude | FastAPI + tunnel; `prophet forecast register --endpoint-url` succeeds | 60 min | conditional |
| Live-event prediction loop | Claude | predictions submitted within 5 min of new event posting | depends | conditional |
| Write Project Story | Claude | `submission/PROJECT_STORY.md` ready | done | ✅ |
| Calibration plot per category | Claude | `reports/calibration.png` shows reliability diagram | 30 min | low priority |
| Pre-submit verify | Claude + Rob | `./scripts/agent/verify.sh` green; fresh venv install works | 30 min | day-2 |

---

## How we'll know we're making progress

**Quantitative signals (Brier on `sample-resolved`, n=26):**
- 0.250: random — anything above this is broken
- 0.219: uniform prior — minimum-viable bar
- 0.200: weak LLM — okay
- 0.190: ✅ our V1 baseline — current floor
- 0.170: strong; would be a real result
- 0.150: great; suggests retrieval or strong ensemble effect
- < 0.130: extraordinary; sanity-check the data

**Qualitative signals:**
- Each new variant has rationales that read like a calibrated forecaster, not a hedging boilerplate generator.
- The leaderboard mentions us by team `CanadaHacks` once we submit predictions.
- The `prophet trade dashboard` view (if available for the forecast track) shows our predictions live.

**Red flags:**
- Brier flat across model swaps → prompt is the bottleneck, not the model.
- Brier worse with stronger model → we're overfitting prompt tricks the model doesn't follow.
- Many fallback-to-uniform-prior log lines → an API gotcha (token cap, model name, parameter rejection) is silently degrading us. Fix before rerunning.

---

## Multi-agent collaboration

`docs/WORKTREE_PROTOCOL.md` is the existing framework: each non-Rob coding agent works in `.claude/worktrees/<descriptor>/` on `feat/<dated-slug>` branches; never touches `main` directly; ownership of `agent.py`/`config.yaml`/`forecast_track.py` claimed via `docs/AGENT_STATUS.md`.

**Concrete uses for parallel agents right now:**

1. **Research agent (Codex or another Claude)** — small worktree. Goal: read the three references Rob surfaced (arxiv 2503.01307, the ACX "shameless guesses" post, e-values literature) and summarize anything actionable for our prompts. Output: `docs/research_notes.md`.
2. **Frontend / dashboard agent** — small worktree. Goal: wrap `data/predictions/backtest_summary.json` in a static HTML page that auto-refreshes when the file updates. Doesn't touch any production path.
3. **Retrieval-augmented variant** — separate worktree off `feat/retrieval-v1`. Owns `retrieval/` directory creation. PRs back to main.

Each worktree gets a short stop-signal contract:
- `pass` — done, verify.sh green, PR open
- `block: <reason>` — couldn't finish; flagged in `docs/AGENT_STATUS.md` for Rob to resolve
- `no-change-needed` — looked at the problem, nothing to do

---

## What we still don't know (open questions)

1. **Where does the actual submission happen?** Likely `prophet forecast register --endpoint-url URL` is the production path (server calls our endpoint when events resolve). No `prophet forecast submit` exists. **Need to ask on Discord:** is a one-shot file upload supported, or is the endpoint URL mandatory?
2. **What model does the leaderboard's main column measure?** It's not Brier (Brier would be 0.0-0.5, leaderboard shows 0.9+). Probably calibrated-accuracy or log-score. The last column appears to be Brier and shows top values around 0.02-0.05 — meaningfully better than our 0.19.
3. **Do we need a Google API key for Gemini 3 Pro?** The leaderboard says it's the top fixed-context model. We don't have a key. Question: is one in scope to acquire today, or skip?
4. **Are events scored once-resolved or periodically?** Affects how aggressively we re-submit during the sprint.

---

## Reproducibility

```bash
git clone git@github.com:Robby955/prophet-hacks.git
cd prophet-hacks
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
bash scripts/sync_env_from_variables.sh  # pulls keys from ~/Desktop/variables.txt

# Pull resolved dataset and build actuals
prophet forecast retrieve --dataset sample-resolved --include-resolved -o data/resolved.json
python scripts/build_actuals.py data/resolved.json data/actuals.json

# Backtest every variant
python scripts/backtest_forecast.py \
    --events data/resolved.json \
    --actuals data/actuals.json \
    --variants uniform_prior,single_llm,opus_47,gpt55,ensemble_logit
```
