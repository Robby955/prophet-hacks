# Documentation index

Headline number: the honest, leakage-disciplined binary Brier is 0.118
(date-capped retrieval). The 0.038 replay figure is best-case-with-hindsight
and is only ever shown labeled as such; never as the headline.

Start here:

- `README.md`: public overview, architecture, run commands, headline results.
- `submission/REPORT.md`: technical submission report.
- `submission/PROJECT_STORY.md`: Devpost-style project narrative.
- `docs/FINDINGS.md`: empirical findings, caveats, and negative results.
- `static/summary.pdf`: visual report for quick sharing.
- `static/summary.html`: browser version of the visual report.
- `static/overview.html`: at-a-glance results overview (honest headline 0.118).
- `static/diagnostics.html`: calibration and residual diagnostics page.

Operations:

- `docs/LIVE_OPERATIONS.md`: production operations notes.
- `docs/RUNBOOK.md`: incident response and first-call checks.
- `docs/ROADMAP.md`: post-submit roadmap.
- `scripts/full_check.sh`: end-to-end live audit.
- `scripts/preflight.sh`: deploy preflight gate.
- `scripts/agent/deploy.sh`: Railway deploy wrapper.

Research and reproducibility:

- `docs/DECISIONS.md`: append-only decision log and postmortems.
- `docs/RESEARCH_QUEUE.md`: post-submit experiment queue.
- `docs/WORKSHOP_PAPER_DRAFT.md`: post-event paper draft.
- `docs/QUANT_PORTFOLIO_ARTIFACTS.md`: portfolio-quality artifact checklist.
- `docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md`: fill after live scoring.
- `data/predictions/`: saved backtest and ablation outputs.
- `research/`: pre-event strategy notes and longer-form reference material.

Analysis and resolution tooling:

- `scripts/diagnostics.py`: builds the calibration/residual diagnostics
  (powers `static/diagnostics.html`).
- `scripts/ablate_search_provider.py`: paired search-provider bake-off; isolates
  retrieval-freshness leakage (the 0.038 vs 0.118 gap).
- `scripts/leakage_label.py`: labels retrieved URLs with post-resolution markers
  for the leakage audit.
- `scripts/abstain_sweep.py`: sweeps the abstain threshold (powers the abstain
  slider page).
- `scripts/generate_sports_slate.py`: generates the resolvable sports event slate.
- `scripts/auto_resolve_sports.py`: auto-resolves sports events to actuals.
- `scripts/auto_resolve_finance.py`: auto-resolves finance/macro events to actuals.

Submission support:

- `submission/DEVPOST_PASTE.md`: submitted Devpost copy, with private access
  details redacted in the public repository.
- `submission/DEMO_SCRIPT.md`: screen-recording plan.
- `submission/TTS_SCRIPT.md` and `submission/TTS_SCRIPT_V2.md`: demo narration
  scripts.
