import math

import pytest

import forecast_track


def _event() -> dict:
    return {
        "market_ticker": "TEST-ENSEMBLE",
        "title": "Will the ensemble test pass?",
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    }


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def test_ensemble_logit_uses_anthropic_and_openai_models(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    outputs = {
        ("anthropic", forecast_track._FORECAST_MODEL): {
            "p_yes": 0.20,
            "rationale": "anthropic read",
        },
        ("openai", forecast_track._OPENAI_FORECAST_MODEL): {
            "p_yes": 0.80,
            "rationale": "openai read",
        },
    }

    def fake_predict(event: dict, *, model: str, vendor: str) -> dict:
        assert event["market_ticker"] == "TEST-ENSEMBLE"
        calls.append((vendor, model))
        return outputs[(vendor, model)]

    monkeypatch.setattr(forecast_track, "_predict_one_model", fake_predict)

    result = forecast_track.predict_ensemble_logit(_event())

    assert calls == [
        ("anthropic", forecast_track._FORECAST_MODEL),
        ("openai", forecast_track._OPENAI_FORECAST_MODEL),
    ]
    assert result["p_yes"] == pytest.approx(
        _sigmoid((_logit(0.20) + _logit(0.80)) / 2.0),
    )
    assert "ensemble logit-mean" in result["rationale"]
    assert "anthropic read" in result["rationale"]
    assert "openai read" in result["rationale"]


def test_ensemble_logit_clamps_extreme_model_probabilities(monkeypatch) -> None:
    outputs = [
        {"p_yes": 0.0, "rationale": "below clamp"},
        {"p_yes": 1.0, "rationale": "above clamp"},
    ]

    def fake_predict(_event: dict, *, model: str, vendor: str) -> dict:
        return outputs.pop(0)

    monkeypatch.setattr(forecast_track, "_predict_one_model", fake_predict)

    result = forecast_track.predict_ensemble_logit(_event())

    assert result["p_yes"] == pytest.approx(0.5)


def test_ensemble_leaderboard_uses_three_expected_models(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    outputs = {
        ("anthropic", forecast_track._FORECAST_MODEL): {
            "p_yes": 0.30,
            "rationale": "sonnet",
        },
        ("anthropic", forecast_track._OPUS_46_MODEL): {
            "p_yes": 0.60,
            "rationale": "opus",
        },
        ("openai", forecast_track._GPT52_MODEL): {
            "p_yes": 0.90,
            "rationale": "gpt",
        },
    }

    def fake_predict(event: dict, *, model: str, vendor: str) -> dict:
        assert event["market_ticker"] == "TEST-ENSEMBLE"
        calls.append((vendor, model))
        return outputs[(vendor, model)]

    monkeypatch.setattr(forecast_track, "_predict_one_model", fake_predict)

    result = forecast_track.predict_ensemble_leaderboard(_event())

    assert calls == [
        ("anthropic", forecast_track._FORECAST_MODEL),
        ("anthropic", forecast_track._OPUS_46_MODEL),
        ("openai", forecast_track._GPT52_MODEL),
    ]
    assert result["p_yes"] == pytest.approx(
        _sigmoid((_logit(0.30) + _logit(0.60) + _logit(0.90)) / 3.0),
    )
    assert "ensemble3 logit-mean" in result["rationale"]
    assert "sonnet" in result["rationale"]
    assert "opus" in result["rationale"]
    assert "gpt" in result["rationale"]


def test_ensemble_leaderboard_rationale_is_capped(monkeypatch) -> None:
    long_rationale = "x" * 500

    def fake_predict(_event: dict, *, model: str, vendor: str) -> dict:
        return {"p_yes": 0.55, "rationale": long_rationale}

    monkeypatch.setattr(forecast_track, "_predict_one_model", fake_predict)

    result = forecast_track.predict_ensemble_leaderboard(_event())

    assert len(result["rationale"]) <= 300
