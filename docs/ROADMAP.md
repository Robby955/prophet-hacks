# Roadmap - post-submit

Status as of 2026-05-17:

- Public repository and public landing page are live.
- Forecasting endpoint is active at `https://agent.forecastingpath.com/predict`.
- Production variant is `multi_outcome_retrieval`.
- Offline results are documented in `README.md`, `submission/REPORT.md`,
  `docs/FINDINGS.md`, and `static/summary.pdf`.
- Live Prophet Arena scoring is still the decisive evidence. Do not treat
  offline Brier as a final market-beating claim.

## P0 - Eval-window monitoring

1. Watch for the first Prophet Arena call.
2. Inspect the stored trace immediately: payload shape, outcome labels,
   retrieval quality, parse path, warnings, and latency.
3. Confirm `/healthz.commit` matches the expected deployed commit before
   attributing any behavior to current code.
4. Preserve raw traces, but redact any private tokens or non-public payload
   details before publishing.
5. Run `scripts/analyze_results.py` when scored actuals are available.

## P1 - Low-risk public artifact polish

- Keep `docs/INDEX.md` as the canonical map of public artifacts.
- Keep the dashboard PIN out of tracked public docs.
- Fix `www.forecastingpath.com` so it either serves the same Railway app or
  does not serve an unrelated project.
- Add HTTP `HEAD` handling for `/` and health routes if uptime probes need it.
- Keep `README.md`, `submission/REPORT.md`, and `docs/FINDINGS.md`
  numerically consistent after live results arrive.

## P2 - Research after live data

- Compare team Brier against the market baseline on the actual live event set.
- Test market-aware abstention only if live payloads expose reliable market
  prices or traces can reconstruct them without leakage.
- Replicate any prompt or retrieval ablation before promoting it. On the
  small `sample-resolved` set, sub-0.01 Brier deltas are not enough.
- Run a chronological replay benchmark before making broad claims about
  retrieval quality on resolved events.
- Revisit the workshop-paper draft after live score, leakage audit, and
  market-baseline comparison are all in one table.

## Do not do without fresh evidence

- Do not change the production forecast prompt, model, retrieval count, or
  post-processing because a single offline run looks better.
- Do not claim market-beating performance until live Team Brier versus Market
  Brier exists.
- Do not rewrite `docs/DECISIONS.md` to make the process look cleaner. It is
  intentionally append-only.
- Do not expose dashboard secrets in screenshots, docs, videos, or public
  issue comments.
