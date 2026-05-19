# Shadow Calibration Agent Brief

## Goal

Add clean forward events that let us test forecasting behavior before outcomes
resolve. This is a research loop only. Do not change the production endpoint,
Railway config, Prophet Arena registration, prompt, model, or deployed code.

## Files

- `data/shadow_calibration/events.json`: queued events.
- `logs/shadow_calibration.jsonl`: generated forecast records, git-ignored.
- `data/shadow_calibration/resolutions.json`: manual winners after events resolve.
- `scripts/shadow_calibration.py`: runs queued events against `/predict`.
- `scripts/score_shadow_calibration.py`: scores resolved logged forecasts.
- `scripts/render_shadow_dashboard.py`: renders `logs/shadow_calibration.html`.

Agents adding events may edit only:

- `data/shadow_calibration/events.json`
- `data/shadow_calibration/resolutions.json`, only after an event resolves

Do not edit forecasting code, scoring scripts, dashboard rendering, model
prompts, production endpoint config, Railway variables, or historical logs
unless explicitly asked. Do not commit `logs/*.jsonl` or `logs/*.html`.

## Event Quality Rules

Add events only when all of these are true:

1. The event has not started or resolved yet.
2. The resolution rule is objective and externally checkable.
3. The close time is explicit and in UTC.
4. The outcome labels are exact and stable.
5. There are at least two source URLs: one schedule/reference source and one
   market, odds, quote, or official-resolution source when available.
6. The event adds useful variety by domain, horizon, or outcome shape.
7. The market snapshot has an `as_of` timestamp in UTC and concrete notes.

Prefer events resolving in 6 hours to 7 days. Use a few 7-14 day macro or
market events when they are unusually clean.

Do not rename `event_ticker` or outcome labels after a forecast has been
logged. The scorer matches resolved winners against the forecast probability
`market` labels, so spelling must be exact.

## Good Event Types

- MLB/NBA/NHL/WNBA games with official schedule and odds pages.
- Soccer cup/final matches where extra time and penalties can be specified.
- Public equity or ETF close thresholds with official market close time.
- Crypto price thresholds at a precise UTC timestamp.
- Scheduled macro releases: jobless claims, consumer sentiment, PMI, CPI, GDP,
  central bank rate decisions.
- Weather events only when the station, metric, and timestamp are explicit.

## Avoid

- Already-started games unless clearly labeled as in-game and excluded from
  clean calibration.
- Vague political or news outcomes without a single resolution source.
- Events where multiple outcomes can be simultaneously true unless the rules
  explicitly use marginal scoring.
- Thresholds far away from current market prices unless the purpose is a
  longshot-specific test.
- Any production deployment, environment variable, or `/predict` code change.
- Post-resolution articles as if they were pre-event evidence.
- Backfilled odds presented as if they were captured before the event.

## JSON Shape

Each event in `data/shadow_calibration/events.json` must include:

```json
{
  "event_ticker": "SHADOW-DOMAIN-SLUG-YYYYMMDD",
  "market_ticker": "SHADOW-DOMAIN-SLUG-YYYYMMDD",
  "title": "Question in plain English?",
  "description": "One or two sentences with the date and resolution context.",
  "category": "Sports | Financial Markets | Crypto | Macro | Weather | ...",
  "close_time": "2026-05-21T12:30:00Z",
  "outcomes": ["Yes", "No"],
  "rules": "Exact resolution rule.",
  "market_snapshot": {
    "as_of": "2026-05-19T05:30:00Z",
    "source_urls": ["https://...", "https://..."],
    "notes": "What was known when queued."
  },
  "notes": "Why this is useful for calibration."
}
```

Implementation notes:

- `close_time` must include timezone, preferably UTC `Z`.
- The runner sends only endpoint-safe fields: `event_ticker`,
  `market_ticker`, `title`, `description`, `category`, `close_time`,
  `outcomes`, and `rules`.
- `market_snapshot` and `notes` stay local for audit/dashboard context.
- A source page may update after the event. Record the observed facts and the
  timestamp in `market_snapshot.notes`.

## Resolution Shape

After resolution, prefer the object form in
`data/shadow_calibration/resolutions.json`:

```json
{
  "SHADOW-MLB-TOR-NYY-20260519": {
    "winner": "New York Yankees",
    "resolved_at": "2026-05-20T02:15:00Z",
    "source_urls": ["https://www.mlb.com/gameday/..."],
    "notes": "Official final score source."
  }
}
```

A bare string winner works, but object form is better for auditability.

## Commands

Validate and preview:

```bash
.venv/bin/python -m json.tool data/shadow_calibration/events.json >/tmp/events.json
.venv/bin/python scripts/shadow_calibration.py --dry-run --limit 20
.venv/bin/python -m pytest tests/test_shadow_calibration.py tests/test_score_shadow_calibration.py tests/test_render_shadow_dashboard.py
```

Run new queued events:

```bash
.venv/bin/python scripts/shadow_calibration.py --limit 20
```

After outcomes resolve, fill `data/shadow_calibration/resolutions.json`, then:

```bash
.venv/bin/python scripts/score_shadow_calibration.py
.venv/bin/python scripts/render_shadow_dashboard.py
```

Open the local view:

```bash
open logs/shadow_calibration.html
```

## Do Not Touch

- Do not change the production endpoint because of a small shadow sample.
- Do not run with `--force` unless intentionally retrying a failed row.
- Do not run with `--include-closed` for clean calibration.
- Do not alter `logs/shadow_calibration.jsonl` to fix history.
- Do not broaden event semantics after forecast time.

## Reporting

Return a short summary with:

- event IDs added
- domains covered
- close-time range
- source URLs used
- dry-run and test results
- whether any event was excluded and why

Do not claim model improvement from unresolved events. The point is to collect
pre-resolution forecasts first, then score them after resolution.
