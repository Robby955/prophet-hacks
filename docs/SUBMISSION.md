# Hackathon submission — execution guide

What Rob needs to do when the submission window opens. Everything is
ready on our side; the form just needs the URL.

## The submission form

**Submit at:** <https://www.prophethacks.com/submit-endpoint>

(Button is also on the hackathon mainpage: <https://www.prophethacks.com>)

Auth: name + email used at check-in.

## What to enter in the form

| Field | Value |
| --- | --- |
| Endpoint URL | `https://agent.forecastingpath.com/predict` |
| Team / Project | `CanadaHacks` / `The Oracles` |
| Model name (if asked) | `claude-opus-4-7` (the actual forecast model) or `multi_outcome_retrieval` (our variant name) |

The form's exact field labels may differ; the key piece is the **endpoint URL**.

## Pre-submission readiness checklist

Before submitting, confirm everything works:

```bash
./scripts/full_check.sh
```

Expected: all checks pass. The script reports one OK line per check
(some steps emit multiple, e.g. summary.html + summary.pdf). The
total OK count varies by environment; the key thing is the
`FAIL` count is `0`. Specifically the checks are:

1. Verify gate green (~200 tests)
2. Working tree clean + HEAD pushed
3. Local HEAD = deployed commit (no drift)
4. `/healthz` returns `{"status":"ok","variant":"multi_outcome_retrieval", ...}`
5. `/predict` end-to-end smoke succeeds (real Brave + Opus call, ~$0.10)
6. `/login` serves the PIN form
7. `/dashboard` redirects browser visitors to `/login`
8. `/predictions` returns 401 to API callers
9. Static artifacts (summary.html + summary.pdf) serve 200
10. Watcher process alive

Last good run (2026-05-16T22:30Z): all checks pass against live;
zero failures.

## What Prophet Arena's call to us looks like

PA sends one event at a time as JSON `POST /predict`:

```json
{
  "event_ticker": "task-001",
  "market_ticker": "task-001",
  "title": "Who will win: Pittsburgh or Atlanta?",
  "subtitle": null,
  "description": "Predict the winner of the scheduled matchup.",
  "category": "Sports",
  "rules": "Resolves to the official winner after the game is final.",
  "close_time": "2026-03-21T23:59:59Z",
  "outcomes": ["Pittsburgh", "Atlanta"],
  "resolved_outcome": null
}
```

We respond with:

```json
{
  "probabilities": [
    {"market": "Pittsburgh", "probability": 0.68},
    {"market": "Atlanta",    "probability": 0.32}
  ]
}
```

The per-event budget is **10 minutes** (Anri Gu, Discord). Our typical
latency is ~5s; the stress test confirmed 5 concurrent calls in 7.2s.

## What PA cannot rely on us doing

- We will NOT see resolved outcomes during the eval window.
- We will NOT receive feedback per call beyond HTTP status.
- We will NOT update our model mid-event (production model is locked
  to `multi_outcome_retrieval` until Rob explicitly promotes a new
  variant).

## What we can do mid-event

- Inspect `/predictions` (PIN required) to see every call we've returned
  including the full pipeline trace (Brave query, latency, parse path).
- Monitor `/healthz.commit` to confirm what code is serving.
- Hit `/predict` ourselves at any time to smoke-test.

## Emergency rollback

If the live agent starts misbehaving mid-event:

```bash
# 1. Find the last known-good commit:
git log --oneline | head -20

# 2. Check out and redeploy:
git checkout <good-sha>
./scripts/agent/deploy.sh "rollback to <good-sha>"
```

If `./scripts/agent/deploy.sh` itself fails (uploads or build issues),
the existing deploy keeps serving — PA never sees the broken build.
See `docs/RUNBOOK.md` for the deploy-failure triage.

## Post-event

- Run `python scripts/analyze_results.py --predictions-url=... --actuals=...`
  once PA publishes resolved outcomes to compute our final Brier.
- Fill in `docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md` within 7 days.
- Consider `https://prophetarena.co/onboarding` for permanent
  leaderboard inclusion (would require building an OpenAI-compatible
  `/v1/chat/completions` adapter; out of scope for the hackathon).

## "What about the OpenAI-compatible API form at prophetarena.co/onboarding?"

Different scope. That's PA's **general platform onboarding** — it
adds your model to the public PA leaderboard as a permanent
participant, scored on PA's full benchmark suite. The form requires:

- Base URL of an OpenAI-compatible `/v1/chat/completions` endpoint
- API key
- Model name

We do NOT need this for the hackathon. We could build a shim later
(translate OpenAI chat-completions requests into our forecast pipeline)
as a post-hackathon project — a 2-3 hour add.
