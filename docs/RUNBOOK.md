# Runbook

What to do when something goes wrong mid-event. Each section is a single
incident pattern with a triage step, a fix, and a recovery check.

For the live forecasting endpoint on Railway, start with
`docs/LIVE_OPERATIONS.md`. This runbook still covers the trading skeleton,
SDK lifecycle, and general failure patterns.

---

## Rate-limit hit (HTTP 429)

- **Symptom:** `APIClientError` with `status_code=429`, or `Retry-After` header observed in logs.
- **What happens automatically:** `ServerAPIClient` retries up to 3 times with jittered backoff, honoring `Retry-After`.
- **Manual action if the retries are exhausted:**
  1. Switch the forecast model to the next entry in `models.fallback_chain` by editing `config.yaml` (the change takes effect on the next tick because we recompute `config_hash` each run).
  2. If still rate-limited, lower `MAX_MARKETS_ANALYZED_PER_TICK` to 3 in `risk.py` and rerun.
- **Recovery check:** next-tick trace JSONL contains decision records and no 429 in the runtime log.

## API 5xx from Prophet Arena

- **Symptom:** `APIServerError` after retries.
- **What happens automatically:** `ServerAPIClient` retries up to 3 times. Beyond that the exception propagates and the agent exits non-zero.
- **Manual action:**
  1. Check `https://api.aiprophet.dev/health` (the `health_check` method works without auth).
  2. If the server is unhealthy, wait. Do not loop. The current lease will expire server-side after `lease_sec` (600s default), allowing another claim.
  3. Re-run `python agent.py --slug <same-slug> --variant <same-variant>` to resume.
- **Recovery check:** `get_progress` returns an incremented `completed` count.

## Anthropic / OpenAI 5xx or rate limit on forecast call

- **Symptom:** SDK error inside the forecaster variant.
- **Fallback chain:** `anthropic/claude-opus-4-7` -> `anthropic/claude-sonnet-4-6` -> `anthropic/claude-haiku-4-5-20251001`.
- **Manual action:** wrap the forecast call site in a try/except that walks the chain. The skeleton's `forecaster.forecast(...)` does not yet implement walking; until it does, edit `config.yaml` -> `models.forecast` to the next entry and rerun the tick.
- **Recovery check:** the trace JSONL `model` field for that tick names the fallback model.

## Auth failure (PA_SERVER_API_KEY rejected)

- **Symptom:** HTTP 401 from the very first call.
- **Manual action:**
  1. Confirm `.env` has the kickoff-provided key under `PA_SERVER_API_KEY` (not `PROPHET_API_KEY`).
  2. `source .venv/bin/activate && python -c "import os; print(bool(os.getenv('PA_SERVER_API_KEY')))"` should print `True`.
  3. Reload: `dotenv` is loaded in `agent.main()` so a new shell is not required.
- **Recovery check:** `python -c "from ai_prophet_core import ServerAPIClient; c = ServerAPIClient(base_url='https://api.aiprophet.dev', api_key=open('.env').read().splitlines()[0].split('=',1)[1]); print(c.health_check())"`.

## Partial fill

- **Symptom:** `TradeSubmissionResult.fills` has fewer shares than the intent requested, or `rejections` is non-empty.
- **Treatment:** treat the recorded fill as the position. Do not retry the rejected portion in the same tick. Log the discrepancy in the trace JSONL `notes` field on the next tick that touches the same market.
- **Recovery check:** next-tick `get_portfolio` reflects the partial position.

## Submission infrastructure crash (agent dies mid-tick)

- **Recovery:** the lease expires after `lease_sec` (600s default). Re-running `python agent.py --slug <same-slug>` will create-or-get the existing experiment and claim a new tick. No trace records written before the crash are lost; they remain on disk under `trace/<slug>/`.
- **Recovery check:** count the JSONL files under `trace/<slug>/` and confirm one per claimed tick.

## Coding agent broke `main`

- **Triage:** `git log --oneline -5` to identify the bad commit.
- **Roll back:** `git checkout main && git reset --hard <last-known-good-sha>`. The known-good seed is `15c0ae0` (initial skeleton).
- **Avoid:** never `git push --force` to a shared remote. If the bad commit is local-only, a hard reset is safe; if it is pushed, branch off the last-good SHA and force-push the new branch instead.
- **Recovery check:** `python agent.py --slug smoke --dry-run` exits 0 and prints the expected config hash.

## Discord / Slack escalation

- When in doubt, ask the organizers. Specifically:
  - Is the sandbox endpoint the same as the live endpoint?
  - Are there per-team API spend caps we should know about?
  - Who is paying the LLM bill (us or the platform)?
- Pre-event questions are listed in `docs/PRE_EVENT_CHECKLIST.md`.

---

## Live forecasting endpoint runbook (2026-05-16 onward)

This is the runbook for `agent.forecastingpath.com/predict` — the live
Prophet Arena forecasting endpoint. For trading-track incidents, use the
sections above; for forecast endpoint incidents, use this one.

### How to verify the system is healthy

```bash
# 1. Endpoint is up + which code is serving
curl -s https://agent.forecastingpath.com/healthz | jq .
# expect status=ok, variant=multi_outcome_retrieval, commit=<latest>

# 2. End-to-end pipeline works
python scripts/stress_test.py --n 1
# expect: 1/1 succeeded; max single-call latency < 60s

# 3. Multi-call concurrency works (in case PA queues multiple)
python scripts/stress_test.py --n 5 --workers 5
# expect: 5/5 succeeded; max latency well under PA's 600s budget
# 2026-05-16 baseline: 5 concurrent done in 7.2s wall clock
```

### "Prophet Arena hasn't called us"

Symptom: `/forecast/endpoints/CanadaHacks` shows `last_run_at: null`
hours into the event window.

Triage:
1. Confirm endpoint is registered + active. The CLI:
   `prophet forecast leaderboard` and check we appear.
2. Confirm `/healthz` returns 200 and the correct variant.
3. Confirm PA has open events: `prophet forecast events --status open`.
   If 0 open events, this isn't our problem.
4. Check Railway service logs for any errors during a recent window.
5. The watcher (`scripts/watch_predictions.sh`, PID logged at
   `/tmp/oracles_watch.log`) mac-notifies on first call, open events
   appearing, or scores posting.

No action needed if PA simply hasn't started; wait.

### "/predict is timing out"

Symptom: PA reports `endpoint timeout` or our pipeline takes > 60s.

PA budget per event: 600s (10 minutes). Our 2026-05-16 baseline is ~5s
per call, ~7s for 5 concurrent. If we're hitting > 60s something is
wrong.

Triage:
1. Hit `/predict` ourselves with a synthetic event:
   `python scripts/stress_test.py --n 1`
2. Inspect the trace via `/predictions` (auth required):
   ```bash
   curl -s "https://agent.forecastingpath.com/predictions" \
     -H "x-dashboard-token: $DASHBOARD_AUTH_TOKEN" | jq '.predictions[0].trace.latency_ms'
   ```
   This shows per-stage latency (brave / llm / total).
3. If Brave is slow (> 5s typical): Brave Search may be rate-limited.
   Check Brave quota.
4. If LLM is slow (> 30s): Anthropic API may be degraded. Check
   `https://status.anthropic.com`.
5. Worst case: trip `BRAVE_SEARCH_API_KEY` to empty to force the
   no-retrieval `predict_multi_outcome` fallback path. We lose evidence
   but keep responding. Set the env var back when Brave recovers.

### "/predict returns probabilities=[]"

This is the catastrophic failure mode. Means our pipeline got an event
with `outcomes` empty AND no title we could infer outcomes from.

Triage:
1. Check `/predictions` for the event ticker. Look at `trace.warnings`
   and `trace.outcomes_inferred`.
2. If `outcomes_inferred=true`, the safety net fired and we tried Haiku
   inference but failed.
3. If `outcomes_inferred=false`, the event came in with outcomes already.
   This shouldn't fail — investigate the rationale field for context.

The 2026-05-16 safety net (binary heuristic + Haiku fallback + last-resort
["Yes", "No"]) means this should never happen unless title AND outcomes
are both empty.

### "Deploy failed"

If `./scripts/agent/deploy.sh` fails:
1. **Preflight failed:** read the error, fix it (untracked file? push HEAD?).
2. **Upload TLS error (BadRecordMac / Broken pipe):** transient network.
   Retry the deploy script directly. If it persists, check `du -sh`
   on what would be uploaded — if > 10MB, that's the bug from 2026-05-16.
3. **Build failed on Railway:** check the build log URL the script prints.
   Most common cause: missing dep in `requirements.txt`.
4. **Healthcheck failed:** the new code starts but `/healthz` returns
   non-200. Roll back by deploying the previous commit:
   `git checkout <previous_sha> && ./scripts/agent/deploy.sh "rollback"`.

### "Dashboard shows wrong commit"

`/healthz.commit` should match the latest commit on `main` (and
`scripts/preflight.sh` reports both side-by-side before deploy).

If they don't match:
1. Latest deploy probably didn't land. Check
   `railway deployment list --service oracles-agent --json` for
   FAILED or REMOVED entries near the top.
2. Re-deploy via `./scripts/agent/deploy.sh` (NOT raw `railway up`).
3. After ~2 min, `/healthz.commit` should reflect the new SHA.

### Cost tracking

Per-call cost in production (multi_outcome_retrieval):
- Brave Search: free tier 2k/month; we use ~30/day at hackathon scale
- Anthropic Opus 4.7: ~$0.10/call (5500 in + 500 out tokens)
- Total per event: ~$0.10

Session budget for the 2026-05-16 hackathon: ~$50 expected, well under
the $200/10d threshold that triggers RunPod OSS-hosting consideration
(see `docs/RUNPOD_POSTURE.md`).

Spend is visible on the dashboard "API spend" tile (in-memory, resets on
restart).
