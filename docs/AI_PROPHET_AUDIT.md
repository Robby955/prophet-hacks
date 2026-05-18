# Upstream `ai-prophet/ai-prophet` audit

Read-only audit of the open-source platform we built against during Prophet
Hacks 2026. Goal: document upstream contract gaps we hit, propose minimal
docs-level PRs we could file post-event, and flag any divergences between
what their code does and what their docs claim.

**Posture**: this is a portfolio + contribution prep doc. No PRs filed.
No issues opened. No upstream behavior assumed beyond what their public
code and docs state, plus what we observed at our `/predict` endpoint
during onboarding.

**Scope of audit**: `github.com/ai-prophet/ai-prophet` at upstream HEAD
`84fbd60` (synced 2026-05-17). Our local fork is `Robby955/ai-prophet`
in `~/Desktop/ai-prophet/`.

## Repo health, in one paragraph

Active. Created 2026-03-08, ~2 MB tracked, Python primary, **3 stars** as
of audit time (so they're early — submissions to this repo are likely to
be noticed). Recent merge cadence: PR #23 (schema-doc alignment), PR #21
(dashboard), PR #9 (agent skills), PR #26 (sandbox-search conflict
resolution) all landed 2026-05-16, the day before the hackathon. The PA
team (`listar2000`, `llu0`) plus a Claude bot for conflict resolution are
the visible contributors. **Zero open issues** at audit time. Good
contribution surface for portfolio purposes.

## Modules surveyed

- `packages/core/ai_prophet_core/forecast/schemas.py` — Pydantic models
- `packages/core/ai_prophet_core/forecast/evaluate.py` — Brier scorer
- `packages/core/ai_prophet_core/forecast/dataset_retrieve.py` — sample
  dataset puller
- `packages/core/ai_prophet_core/forecast/retrieve.py`
- `packages/core/ai_prophet_core/forecast/kalshi_client.py`
- `packages/cli/ai_prophet/main.py` — CLI entry point
- `docs/build_a_bot.md` (trading-track focused)
- `docs/using_sample_datasets.md` (recently updated)
- `docs/sandboxed-search.md` (recently added)
- `prophet-agent/agent.py` (single-file example)
- `skills/` (Claude-skill packaging, recently added)

## Findings

### F1. Forecasting-track endpoint contract is undocumented in the repo

`docs/build_a_bot.md` is titled "Building a Trading Bot" and covers the
**trading track only** (tick lifecycle, trade intents, slug, n_ticks,
deadlines, candidate sets). No equivalent document for the
forecasting-track endpoint contract exists in the repo.

What the forecasting endpoint actually needs, gathered from Discord +
implicit behavior, not from the repo:
- `GET /health` to wake the service before scoring
- `POST <your-url>` (any path, typically `/predict`) with an `Event`
  payload
- Response: `{"probabilities": [{"market", "probability"}, …],
  "rationale": "..."}` OR `{"p_yes": float, "rationale": "..."}`
- 10-minute timeout per request batch
- Eval period 2 weeks post-event, continuous

Right now this is splintered across Devpost copy, the
`prophethacks.com/submit-endpoint` page, and Discord clarifications from
Leon (listar2000) on 2026-05-17.

**PR candidate**: a `docs/build_a_forecasting_endpoint.md` parallel to
`build_a_bot.md`, with the same shape: install, mental model, minimum
viable endpoint, request/response examples, common pitfalls.

### F2. Devpost says "OpenAI-compatible," Discord and SDK say event-shape

Devpost's official Forecasting-track entry says:
> "your agent must expose an **OpenAI-compatible** HTTP endpoint the
> evaluation harness can query (10-minute response window per request
> batch)."

Discord (Leon, 2026-05-17 16:58 CDT):
> "You will be providing a forecast endpoint, sth like
> https://xxx.com/predict ... The request format follows the
> **Event Input Shape** in
> https://www.prophethacks.com/forecast-quick-start"

The repo's `Event` and `Prediction` pydantic models support the
event-shape interpretation. Nothing in the repo points at an OpenAI
chat-completions-shaped contract. We built **both** paths (`/predict`
for event-shape, `/v1/chat/completions` for OpenAI-compatible) as
insurance.

**PR candidate**: a one-paragraph clarification in the repo README or in
a new `docs/forecasting_track.md` stating that the contract is the
event-shape POST defined in `forecast/schemas.py:Event`, not OpenAI
chat-completions. Resolves an onboarding ambiguity that cost real teams
implementation time this weekend.

### F3. Event schema requires `category`, but live calls sometimes omit it

`packages/core/ai_prophet_core/forecast/schemas.py:14-23`:

```python
class Event(BaseModel):
    event_ticker: str
    market_ticker: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    category: str            # ← REQUIRED
    rules: str | None = None
    close_time: datetime
    outcomes: list[str] | None = None
    resolved_outcome: dict[str, Any] | None = None
```

We had `category: str` in our server and started returning 422 on PA's
test events. Made it `Optional[str] | None = None` plus
`model_config = ConfigDict(extra="allow")` to recover. This means
**PA's live event harness sends payloads that don't match the
published schema**. Either the live harness or the schema is the wrong
source of truth.

**PR candidate**: smallest possible change — make `category` Optional
in `schemas.py` to match observed harness behavior. One-line PR.
Includes test. Includes a note in the docstring that the field is
informational, not load-bearing.

### F4. `outcomes` is Optional but most downstream code assumes it's present

Same schema, line 22: `outcomes: list[str] | None = None`. But
`_actual_market` in `evaluate.py` and the entire multi-outcome scoring
branch assume `outcomes` is populated. A nullable `outcomes` would crash
the evaluator.

**PR candidate**: either tighten to `list[str] = Field(min_length=2)`
(safer), or document why nullable is allowed (e.g., binary-only events
in some datasets). The current state is ambiguous.

### F5. Evaluator branches on `probabilities` presence; mixing with `p_yes` fails

`packages/core/ai_prophet_core/forecast/evaluate.py:51-66`:

```python
def _prediction_brier(prediction, actual):
    if prediction.probabilities:
        actual_market = _actual_market(actual)  # expects LABEL
        probabilities = {p.market: p.probability for p in prediction.probabilities}
        if actual_market not in probabilities:
            raise ValueError(f"Actual outcome {actual_market!r} missing...")
        return sum(...)  # multiclass Brier

    if prediction.p_yes is None:
        raise ValueError(...)
    return (prediction.p_yes - _actual_binary(actual)) ** 2  # binary
```

**Observed behavior**: when our saved predictions had BOTH `p_yes` AND
`probabilities`, the evaluator routed to the multiclass branch. But our
`actuals.json` stores binary `0.0`/`1.0`. The multiclass branch can't
find `"0.0"` in `{"Kaja Najzer", "Anna Lena Ebster"}` and raises
`ValueError`.

This means **`prophet forecast evaluate`'s reported Brier on the
sample-resolved set must run through a different code path than the
SDK's `score()` function**, or the actuals file format we used differs
from what the CLI expects. Either way, the developer experience is
fragile: if you save predictions with both fields and run them through
the SDK directly, you crash. Our headline 0.0378 number measures
*something* — but the SDK's score() raises on the same inputs.

**PR candidate** (higher risk, requires test changes): make the
multiclass branch resilient to binary actuals. If `actual` is
`{0.0, 1.0, "yes", "no", "true", "false"}` and the prediction also
has `p_yes`, fall back to the binary path. Alternative: document that
the CLI accepts a specific predictions-file format (only `p_yes`, no
`probabilities`) and the SDK's `score()` requires outcome-label
actuals.

### F6. Scoring rule transparency

We see three different Brier formulations in use:
- **CLI** (`prophet forecast evaluate`): single-binary `(p_yes - 1{outcomes[0] won})^2`, averaged over events
- **SDK `score()`** (when `probabilities` set): proper multi-class
  `sum((p_i - 1_i)^2)` across all outcomes
- **Live PA scoring** (per Anri's Discord 2026-05-17 16:33 CDT and the
  trading-track Sravya note): BSS against snapshotted
  Kalshi/Polymarket prices — `(team_brier - market_brier)`

These three rules **rank the same model lineup differently** on n=26
(we measured this — Opus 4.7 wins single-binary, Opus 4.6 wins
multi-class). Teams need to know which one will determine the public
leaderboard, and this is not in the repo docs.

**PR candidate**: a paragraph in the forecasting docs naming all three
formulations, pointing to where each lives (CLI, SDK, live harness),
and stating the canonical scoring rule for the public leaderboard.
This is the highest-value low-risk PR I see.

### F7. `/health` vs `/healthz`

Leon's Discord (2026-05-17 16:58 CDT):
> "A health test where we will do a **GET request to your /health
> endpoint** ... Note that having a /health endpoint is not required,
> but it can help you if you are using a service like free-tier render."

The repo doesn't mention `/health` or `/healthz` anywhere we can find.
Teams using Railway or Render need the wake-up GET; without docs they
have to guess the path.

**PR candidate**: one sentence in the forecasting docs noting that
PA's harness issues a `GET /health` to wake the service before
POSTing events, and recommending teams expose either `/health` or a
container-platform-specific equivalent.

### F8. Trading-track docs gaps Sravya filled in Discord, not in repo

For completeness, even though we're forecasting-track only:
- Slug prefix `eval_<team-name>`
- `n_ticks=1500` for the 14-day window
- Starting cash `$10000`
- 5pm CDT eval window open

Most of this is in `build_a_bot.md` actually — but the exact eval-time
slug convention and start time only appear in Discord. Worth a small
docs touch-up for the trading-track too, but lower-priority for us.

## Candidate PRs ranked

Format: **value** × **risk** = priority.

| # | PR proposal | Value | Risk | Effort | Why |
|---|---|---|---|---|---|
| 1 | Docs: scoring-rule transparency (F6) | High | Very low | 30 min | The three Brier formulations confuse every team; clarifying which is canonical removes ambiguity that affects model-selection decisions. Pure docs. |
| 2 | Docs: forecasting-track endpoint contract (F1 + F2 + F7 combined) | High | Very low | 1 hr | New `docs/build_a_forecasting_endpoint.md` parallel to `build_a_bot.md`. Resolves the OpenAI-vs-event-shape ambiguity and the `/health` wake-up question in one place. |
| 3 | Schema: make `category` Optional (F3) | Medium | Low | 15 min + test | One-line change. Matches observed live behavior. Test asserts the harness's omitted-category payload parses. |
| 4 | Docs: clarify `outcomes` nullability (F4) | Medium | Very low | 10 min | Either tighten schema or document the nullable case. |
| 5 | Evaluator: graceful binary actuals (F5) | Medium | Medium | 2 hr + tests | Touches the score path; requires careful test coverage. Could land in two stages: doc clarification first, code change later. |

**Recommended order** (if Rob OKs filing): #1 first (highest-value /
lowest-risk pure-docs PR), then #2, then #3-#4 as small follow-ups, then
#5 only with full test coverage.

## What this audit does NOT cover

- We did not audit `betting/`, `arena.py`, or the trading-track tick
  lifecycle code in depth.
- We did not audit the `skills/` packaging or the MCP server.
- We did not audit `kalshi_client.py` for Kalshi-API contract drift.
- We did not run the PA SDK's full test suite against our environment.

These could each become their own audit if we want to expand the
contribution surface.

## Things to consult if updating

- Upstream HEAD ref: `84fbd60`
- Schema source of truth: `packages/core/ai_prophet_core/forecast/schemas.py`
- Evaluator source: `packages/core/ai_prophet_core/forecast/evaluate.py`
- Our local fork: `~/Desktop/ai-prophet/` (synced to upstream main)
- Our fork on GitHub: `Robby955/ai-prophet`

## Filing posture

Per Rob's instruction during the eval window:
- Do not file any upstream PRs yet
- Treat this doc as the curated source of contribution candidates
- Decide post-event which (if any) to escalate to PR

Re-evaluating after the eval window closes 2026-05-31 makes sense for
two reasons: (a) we'll have learned which scoring rule actually matters
from PA's live behavior, (b) a contribution made post-event reads as
"reflection on the platform" rather than "competitive intel."
