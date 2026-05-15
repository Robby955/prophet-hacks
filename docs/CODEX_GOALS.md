# Codex Goals

Durable scoped-completion contracts. Paste a Goal verbatim into Codex.
Each Goal ends with `Verified by ./scripts/agent/verify.sh passing.`,
which runs typecheck, tests (when present), smoke import, and dry-run.

Codex stop signals:
- `pass` -> all verification gates green, work complete
- `block: <reason>` -> a verification step failed; Codex stops and surfaces the failure rather than papering over it
- `no-change-needed` -> Codex ran the analysis and determined no edit improves the system

---

## Goal 1. Bootstrap and smoke-test

Pull `Robby955/prophet-hacks` `main`, run the full pre-event checklist from
`docs/PRE_EVENT_CHECKLIST.md` end to end: create venv, install
`requirements.txt`, verify org model access for `gpt-5.4-mini` and
`claude-opus-4-7`, run the dry-run smoke, run a real-tick smoke against the
live Prophet Arena endpoint, verify the resume behavior by re-running the
same slug.

Verified by `python agent.py --slug bootstrap-check --dry-run` exiting 0
AND a complete 21-field JSONL line landing in
`trace/bootstrap-check/<tick_id>.jsonl`. Stop with `pass` if every step is
green; stop with `block: <step-name>` and the failing stderr otherwise.

Budget: $1 in API spend maximum. If spend looks like it will exceed $1
before the smoke completes, stop with `block: budget-exceeded`.

Verified by `./scripts/agent/verify.sh` passing.

---

## Goal 2. Add targeted retrieval modules

Add `retrieval/<market_class>.py` modules for `sports`, `finance`, and
`political` market classes. Discipline:

- One retrieval query per market, two or three sources maximum, official
  source preferred over aggregators.
- Every URL plus timestamp logged into the JSONL `evidence_urls` field on
  the per-decision trace record.
- Triage with `gpt-5.4-mini`. Retrieval fires only when triage marks the
  market high-uncertainty.

Wire the modules into `forecaster.forecast_model_with_retrieval`. Default
config stays retrieval-disabled; enable per-variant only.

Verified by `python agent.py --slug retrieval-test --variant model-forecast-retrieval`
running cleanly with at least one retrieval URL captured in the JSONL
trace records.

Stop with `no-change-needed` if retrieval fails to beat the
no-retrieval variant on Brier in a 20-tick A/B; otherwise stop with
`pass`.

Verified by `./scripts/agent/verify.sh` passing.

---

## Goal 3. Multi-model agreement gate

Add `forecaster.agreement_gate(p_anthropic: float, p_openai: float) -> float | None`.
Return the averaged forecast only when:

- both models agree on direction (both above 0.5 or both below 0.5), AND
- `abs(p - 0.5) >= 0.10` for both.

Otherwise return `None`, which signals SKIP. Wire into the
`calibrated-ensemble` variant in `forecaster.py`.

Verified by `tests/test_agreement_gate.py` passing the six enumerated
cases (both YES strong, both NO strong, both YES weak, both NO weak,
direction-conflict, magnitude-conflict) AND a 10-tick live run logging
the gate decision in the JSONL `notes` field.

Stop with `pass` if both gates green; `block: <test-name>` if any
enumerated case fails.

Verified by `./scripts/agent/verify.sh` passing.

---

## Goal 4. Calibration backtest

If Prophet Arena exposes historical resolution data via the SDK
(check `ServerAPIClient` for a method exposing closed forecast events
with `actual_outcome`), fetch the last N=200 resolved markets, run each
forecaster variant against them offline, produce calibration plots via
`scripts/build_results_report.py` for each variant, and write a comparison
note to `reports/<timestamp>/calibration_backtest.md`.

Decision rule: paired bootstrap test on Brier deltas, n=200, 95% CI. If
the CI for the best-variant minus runner-up excludes zero, stop with
`pass` plus a one-line recommendation on which variant to ship.

Stop with `block: insufficient-data` if the SDK does not expose
historical labels for at least 100 resolved markets.

Verified by `./scripts/agent/verify.sh` passing.

---

## Goal 5. Final package and submit

Run `scripts/build_results_report.py` against the active experiment slug.

Verify the submission gates from `SUBMISSION_NOTES.md`: pinned packages,
env vars documented in `.env.example`, clean install in a fresh venv,
one-command run, resume behavior, log dirs auto-created, hard limits
live in `risk.py` constants, JSONL traces present for every claimed tick,
zero secrets / caches / virtualenvs in the zip.

Build the final zip via `scripts/package_submission.sh` (write the script
if it does not yet exist; exclude `.venv/`, `logs/*.jsonl`, `trace/`,
`reports/`, `.env`, `__pycache__/`, `.pytest_cache/`, `.git/`).

Verified by extracting the zip into a fresh directory, creating a venv,
running `pip install -r requirements.txt`, then `python agent.py --slug submit-smoke --dry-run`
exiting 0.

Stop with `pass` plus the final zip path AND its sha256 checksum.

Verified by `./scripts/agent/verify.sh` passing.
