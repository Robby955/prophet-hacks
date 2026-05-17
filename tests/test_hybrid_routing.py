import pytest

import forecast_track


def _event(outcomes: list[str]) -> dict:
    return {
        "market_ticker": "TEST-HYBRID",
        "title": "Which outcome resolves?",
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": outcomes,
    }


def test_hybrid_binary_route_uses_gpt55_and_applies_longshot_guard(monkeypatch) -> None:
    calls: list[str] = []

    def fake_gpt55(event: dict) -> dict:
        calls.append(event["market_ticker"])
        return {"p_yes": 0.02, "rationale": "binary longshot"}

    monkeypatch.setattr(forecast_track, "predict_gpt55", fake_gpt55)
    monkeypatch.setattr(
        forecast_track,
        "predict_multi_outcome",
        lambda _event: pytest.fail("multi_outcome should not run for binary event"),
    )

    result = forecast_track.predict_hybrid_routed(_event(["Yes", "No"]))

    assert calls == ["TEST-HYBRID"]
    assert result["p_yes"] == pytest.approx(0.10)
    assert result["probabilities"] == [
        {"market": "Yes", "probability": pytest.approx(0.10)},
        {"market": "No", "probability": pytest.approx(0.90)},
    ]
    assert result["rationale"].startswith("hybrid(binary->gpt55+guard):")


def test_hybrid_binary_route_preserves_duplicate_labels_without_crashing(monkeypatch) -> None:
    monkeypatch.setattr(
        forecast_track,
        "predict_gpt55",
        lambda _event: {"p_yes": 0.65, "rationale": "duplicate labels"},
    )

    result = forecast_track.predict_hybrid_routed(_event(["Yes", "Yes"]))

    assert result["p_yes"] == pytest.approx(0.65)
    assert result["probabilities"] == [
        {"market": "Yes", "probability": pytest.approx(0.65)},
        {"market": "Yes", "probability": pytest.approx(0.35)},
    ]


def test_hybrid_single_outcome_route_returns_certain_single_label(monkeypatch) -> None:
    monkeypatch.setattr(
        forecast_track,
        "predict_gpt55",
        lambda _event: {"p_yes": 0.27, "rationale": "single outcome"},
    )

    result = forecast_track.predict_hybrid_routed(_event(["Only outcome"]))

    assert result["p_yes"] == pytest.approx(1.0)
    assert result["probabilities"] == [
        {"market": "Only outcome", "probability": pytest.approx(1.0)},
    ]


def test_hybrid_zero_outcome_route_returns_legacy_probability_without_probs(monkeypatch) -> None:
    monkeypatch.setattr(
        forecast_track,
        "predict_gpt55",
        lambda _event: {"p_yes": 0.73, "rationale": "no outcomes"},
    )

    result = forecast_track.predict_hybrid_routed(_event([]))

    assert result["p_yes"] == pytest.approx(0.73)
    assert result["probabilities"] == []


def test_hybrid_multi_route_uses_multi_outcome_without_gpt55(monkeypatch) -> None:
    def fake_multi(event: dict) -> dict:
        return {
            "p_yes": 0.20,
            "rationale": "multi route",
            "probabilities": [
                {"market": event["outcomes"][0], "probability": 0.20},
                {"market": event["outcomes"][1], "probability": 0.30},
                {"market": event["outcomes"][2], "probability": 0.50},
            ],
        }

    monkeypatch.setattr(
        forecast_track,
        "predict_gpt55",
        lambda _event: pytest.fail("gpt55 should not run for multi-outcome event"),
    )
    monkeypatch.setattr(forecast_track, "predict_multi_outcome", fake_multi)

    result = forecast_track.predict_hybrid_routed(_event(["A", "B", "C"]))

    assert result["p_yes"] == pytest.approx(0.20)
    assert result["probabilities"] == [
        {"market": "A", "probability": 0.20},
        {"market": "B", "probability": 0.30},
        {"market": "C", "probability": 0.50},
    ]
    assert result["rationale"] == "hybrid(multi->multi_outcome): multi route"
