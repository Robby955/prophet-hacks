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
