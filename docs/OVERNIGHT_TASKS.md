# Overnight tasks · 2026-05-17 02:30 CT → ~10:00 CT

For Codex (or any agent waking up while Rob sleeps). Ordered by
impact-per-risk. All explicitly **non-production**: do not touch
`forecast_track.py:predict_multi_outcome_retrieval`, Railway env
vars, `submission/`, `docs/DECISIONS.md`, `docs/FINDINGS.md`.

If you're picking these up, claim your section in `docs/AGENT_STATUS.md`
first so Rob knows which agent did what.

## C1 (highest leverage) · Fix scatter plot label overlap

`static/scatter_resolved.html` (built from `scripts/build_d1_scatter.py`).
Rob's review: "looks good but has overlapping number in text on the plot
which hurts it." The `n_outcomes` annotations on each point overlap with
neighboring point labels and with each other on dense regions.

Acceptance:
- No label-on-label collision in the default view
- Hover still shows the full title + rationale
- Numbers are visible only on points where `n_outcomes >= 8` (drop the
  small-binary ones), OR use Plotly's `textposition: "middle right"` with
  alpha-fade
- Regenerate and verify the page loads without console errors

Estimated: 30 min, $0.

## C2 · Add explanation copy to interactive pages

The five interactive pages (gallery_resolved, gallery_open, scatter,
heatmap, abstain_slider, bootstrap_hist, pipeline_trace) all assume the
reader knows what they're looking at. Rob's read on the abstain slider:
"a bit dry but seems working." A judge with no context will skip it.

Acceptance for each page: a one-sentence "What you're looking at" panel
or tooltip near the top that explains:
- What the chart shows
- What "good" looks like (where on the chart)
- Where to click for the underlying data

Estimated: 1 hr, $0.

## C3 · Shepherd-style guided tour for /observatory and /dashboard

Rob asked for this directly: "that should be easy for others like a
suggested path or tour using shepard or such." Shepherd.js is small,
permissive-licensed, works without a framework. Add a `?tour=1` query
flag that triggers a 5-step walkthrough:

1. "Live commit + uptime tile" → here's how to verify what's serving
2. "Production variant tile" → here's what's running
3. "Recent predictions" → where live PA calls will land
4. "Experiment board" → measured-not-shipped section
5. "Per-event drill-down" → click into the gallery

Acceptance: tour visible only when `?tour=1` is set; closes cleanly;
doesn't interfere with normal use.

Estimated: 1.5 hr, $0. Defer to post-event if you don't want to ship it.

## C4 · Pipeline trace banner

`static/pipeline_trace.html` is a static replay of one cached event but
isn't labeled as such. Add a banner at the top:

> Cached example: this is the 2026-05-12 Najzer vs Ebster match replayed
> from disk. For a live walkthrough of a fresh prediction, use the
> `/demo/start` SSE stream on the dashboard.

Acceptance: visible on first load, no JS required.

Estimated: 5 min, $0.

## C5 (deferred, only if budget burns) · Bootstrap-resample the headline

We have one bootstrap CI on the Sonnet→Opus delta from
`scripts/bootstrap_brier_ci.py`. Cross-validate by:

- Running it again with three different seeds (20260516, 20260517, 20260518)
- Confirming the CI is stable to ~0.001
- Add a small note to `docs/DECISIONS.md` confirming reproducibility

Acceptance: three independent seeds all give CIs that overlap
substantially with [0.0143, 0.0374]. Numbers and dates in a new
DECISIONS entry.

Estimated: 15 min, $0.

## What NOT to do overnight

- Do NOT change the production forecast variant (`multi_outcome_retrieval`)
- Do NOT delete the market-odds-anchoring block (PR #12 lives or dies
  on Rob's review, not yours)
- Do NOT regenerate `static/summary.pdf` via the broken matplotlib
  renderer (it's been replaced; `build_summary_report.py` now delegates
  to `build_submission_onepager.py`)
- Do NOT flip the GitHub repo to public (Rob's call, ~30 min before
  Devpost submit)
- Do NOT add any LLM-tell prose (`tests/test_public_text_quality.py`
  will catch most of it but stay vigilant)

## Status when Rob comes back

Update this file with what you finished, what you skipped, and any
blockers. Or just drop a line in `docs/AGENT_STATUS.md` under your
agent's section. Rob will read both before submitting.
