"""forecasting/ — v3 modular forecasting layer.

Built on top of the v2 7-stage pipeline and Gemini's BenchmarkSession lifecycle.
Adds Kalshi-paper-informed rules (longshot guard, favorites no-shrink),
Hedge-style expert pool, source credibility scoring, bidirectional elicitation.

Locked thesis: train the operating system, NOT the model.
See docs/V3_OFFLINE_HARNESS.md.
"""
