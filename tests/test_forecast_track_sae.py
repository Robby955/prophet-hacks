from __future__ import annotations

from types import SimpleNamespace

import forecast_track
import scripts.backtest_forecast as backtest_forecast


def _fake_anthropic_client(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **_: SimpleNamespace(
                content=[SimpleNamespace(text=text)],
            ),
        ),
    )


def test_sae_variant_shrinks_raw_probabilities_toward_prior(monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr(
        forecast_track,
        "_brave_search",
        lambda *_args, **_kwargs: [
            {
                "title": "Market odds favor Alpha",
                "url": "https://example.com/odds",
                "snippet": "Alpha is a favorite but still uncertain.",
                "domain": "example.com",
            },
        ],
    )
    monkeypatch.setattr(
        forecast_track,
        "_aclient",
        lambda: _fake_anthropic_client(
            '{"probabilities": {"Alpha": 0.90, "Beta": 0.10}, '
            '"rationale": "Alpha has the stronger evidence."}',
        ),
    )

    result = forecast_track.predict_multi_outcome_retrieval_sae(
        {
            "market_ticker": "TEST-SAE",
            "title": "Will Alpha beat Beta?",
            "category": "Sports",
            "close_time": "2026-06-01T00:00:00Z",
            "outcomes": ["Alpha", "Beta"],
        },
    )

    probs = {p["market"]: p["probability"] for p in result["probabilities"]}
    assert 0.50 < probs["Alpha"] < 0.90
    assert 0.10 < probs["Beta"] < 0.50
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert result["p_yes"] == probs["Alpha"]
    assert "sae" in result
    assert result["_trace"]["sae"]["applied"] is True
    assert result["_trace"]["sae"]["outcomes"][0]["market"] == "Alpha"


def test_sae_variant_is_registered_for_offline_backtests_only() -> None:
    assert "multi_outcome_retrieval_sae" in backtest_forecast.VARIANTS
    assert "predict_multi_outcome_retrieval_sae" in forecast_track.__all__
