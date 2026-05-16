# v3 handoff — what's on disk, what's left

Dispatch service for code tasks is throttled; the v3 build was
written directly to the working tree from a sandboxed orchestrator.
This note tells the next agent (or Rob) exactly what's here and what
remains.

## What's on disk (untracked under `main`)

```
forecasting/
  __init__.py
  market_blend.py        # Kalshi longshot guard, favorites_no_shrink, longshot_proximity, credibility blend, full_blend
  source_scoring.py      # credibility hierarchy + staleness + ensemble + disagreement
  bidirectional.py       # combine, consistency, should_bidirectional gate
  expert_pool.py         # Hedge weights with eta/min_weight/history warm-start
  risk_helpers.py        # required_edge_with_kalshi_adjustment

evaluation/
  __init__.py
  brier.py               # Brier, BSS, Murphy decomp, alpha-vs-market
  ece.py                 # ECE, MCE, reliability bins
  returns.py             # Trade, Sharpe, pnl_by_price_bucket
  no_leakage_check.py    # assert_no_leakage gate

tools/
  __init__.py
  evaluate_offline.py    # 8-variant comparison
  check_compliance.py    # pre-submission gate

scripts/
  run_offline_eval.py    # orchestrator (no-leakage -> eval -> reports)

offline/
  dataset_schema.md
  sample_tasks.jsonl     # 12 synthetic events across 6 domains

examples/
  toy_events.jsonl       # 5-event subset

tests/
  test_market_blend.py
  test_source_scoring.py
  test_bidirectional.py
  test_evaluation.py
  test_no_leakage.py
  test_expert_pool.py

docs/
  V3_OFFLINE_HARNESS.md
  KALSHI_FINDINGS.md     # with TODOs to fill in verbatim page quotes from the PDF

reports/
  status.md              # the "am I on track?" front door

.pr-body-v3.md           # ready-to-use PR body
V3_HANDOFF.md            # this file
```

## What still needs to happen on a real machine

1. **Verify working tree is clean** of stray files from other branches:
   ```bash
   cd ~/Projects/theorempath/prophet-hacks
   git status --short
   ```
   If anything outside the v3 list above shows up, either commit it
   on a different branch first or stash it.

2. **Branch off main and stage the v3 files:**
   ```bash
   git fetch origin --prune
   git checkout main
   git pull
   git checkout -b feat/2026-05-15-prophet-v3-offline-harness-and-kalshi
   git config --local --get user.email   # MUST be robbysneiderman@gmail.com (no dot)
   git add forecasting/ evaluation/ tools/ scripts/ offline/ examples/ tests/test_market_blend.py tests/test_source_scoring.py tests/test_bidirectional.py tests/test_evaluation.py tests/test_no_leakage.py tests/test_expert_pool.py docs/V3_OFFLINE_HARNESS.md docs/KALSHI_FINDINGS.md reports/status.md V3_HANDOFF.md .pr-body-v3.md
   ```

3. **Run the tests and the dry-run eval:**
   ```bash
   pytest tests/ -v
   PROPHET_OFFLINE_MOCK=1 python scripts/run_offline_eval.py
   ```
   Both should pass cleanly. If a test fails, fix it before pushing.

4. **Wire `required_edge_with_kalshi_adjustment` into the actual risk
   gate.** The function is in `forecasting/risk_helpers.py`. The call
   site lives in `forecaster.py`'s `stage_risk_gate` (or wherever the
   trade-or-skip decision happens). The integration is a one-line
   import + swap:
   ```python
   from forecasting.risk_helpers import required_edge_with_kalshi_adjustment
   # ...
   req_edge = required_edge_with_kalshi_adjustment(
       p_market=p_market,
       model_disagreement=disagreement,
       source_staleness=staleness,
       spread=spread,
   )
   ```

5. **Read the Kalshi PDF on `~/Desktop/`** and replace the TODO section
   at the bottom of `docs/KALSHI_FINDINGS.md` with verbatim
   page-anchored quotes for: the >60% loss on <$0.10 contracts, the
   favorite-longshot bias direction, the Maker-vs-Taker numbers, and
   the probability-overweighting + disagreement mechanism. Add the
   paper's BibTeX entry.

6. **Set up `~/Desktop/ProphetHacks/` org hub** (outside the repo):
   ```bash
   mkdir -p ~/Desktop/ProphetHacks/papers
   ln -sf ~/Projects/theorempath/prophet-hacks ~/Desktop/ProphetHacks/repo
   ln -sf ~/Projects/theorempath/prophet-hacks/reports/status.md ~/Desktop/ProphetHacks/status.md
   ln -sf ~/Projects/theorempath/prophet-hacks/docs/PRE_EVENT_CHECKLIST.md ~/Desktop/ProphetHacks/PRE_EVENT_CHECKLIST.md
   ln -sf ~/Projects/theorempath/prophet-hacks/docs/KALSHI_FINDINGS.md ~/Desktop/ProphetHacks/papers/KALSHI_FINDINGS.md
   cp ~/Desktop/kalshi*.pdf ~/Desktop/ProphetHacks/papers/kalshi.pdf || true
   ```
   Add a `~/Desktop/ProphetHacks/README.md`:
   ```markdown
   # Prophet Hacks — Rob's org hub

   Repo:        repo/   (symlink to ~/Projects/theorempath/prophet-hacks)
   Status:      ./status.md
   Kalshi:      papers/kalshi.pdf + papers/KALSHI_FINDINGS.md
   Pre-event:   ./PRE_EVENT_CHECKLIST.md

   ## Saturday kickoff

       cd repo
       python agent.py --slug smoke --dry-run
       python agent.py --slug live-smoke --once
       bash scripts/run_monitor.sh   # http://localhost:8765/live.html
       python agent.py --slug rob-v1

   ## During the event

       make offline-eval     # re-score variants against logged outcomes
       make status           # refresh status.md
       make compliance       # pre-submission gate before zipping
   ```

7. **Update `Makefile`** to add the v3 targets. Append to existing:
   ```makefile
   .PHONY: offline-eval status monitor compliance smoke test

   offline-eval:
       PROPHET_OFFLINE_MOCK=1 python scripts/run_offline_eval.py

   status:
       python -c "import shutil, sys; shutil.copy('reports/status.md', 'reports/status.md')" \
         && echo "status.md last updated $$(date)"

   monitor:
       bash scripts/run_monitor.sh

   compliance:
       python tools/check_compliance.py

   smoke:
       python agent.py --slug smoke --dry-run

   test:
       pytest tests/ -v
   ```

8. **Commit, push, open draft PR:**
   ```bash
   git commit -m "feat(v3): offline pastcasting harness + Kalshi longshot guard + monitoring v2"
   git push -u origin feat/2026-05-15-prophet-v3-offline-harness-and-kalshi
   gh pr create --draft --base main \
     --head feat/2026-05-15-prophet-v3-offline-harness-and-kalshi \
     --title "feat(v3): offline pastcasting harness + Kalshi longshot guard + monitoring v2" \
     --body-file .pr-body-v3.md
   ```

That's the full v3 handoff. Everything modular, no auto-merge, ready for
Rob to review when he sits down.
