"""Tests for the outcomes-missing safety net added to forecast_track.py
on 2026-05-16 after discovering PA's /forecast/events endpoint returns
all 18 closed events with outcomes=[]. If the live /predict webhook
sends the same light shape, our pipeline would catastrophically degrade
to empty probabilities. The safety net infers outcomes when missing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import forecast_track


# -- Pass-through when outcomes present ----------------------------------


def test_inference_passthrough_when_outcomes_present():
    out = forecast_track._infer_outcomes_when_missing({
        "title": "Will X happen?",
        "outcomes": ["Apple", "Banana", "Cherry"],
    })
    assert out == ["Apple", "Banana", "Cherry"]


def test_inference_passthrough_does_not_call_llm():
    """When outcomes are present, we must NOT call any LLM."""
    with patch.object(forecast_track, "_aclient") as mock_client:
        forecast_track._infer_outcomes_when_missing({
            "title": "anything", "outcomes": ["A", "B"],
        })
        mock_client.assert_not_called()


# -- Binary heuristic ----------------------------------------------------


def test_binary_will_question():
    out = forecast_track._infer_outcomes_when_missing({
        "title": "Will the Fed cut rates in December 2026?",
        "market_ticker": "test-1",
    })
    assert out == ["Yes", "No"]


def test_binary_does_question():
    out = forecast_track._infer_outcomes_when_missing({
        "title": "Does Apple ship the Vision Pro 2 by 2027?",
    })
    assert out == ["Yes", "No"]


def test_binary_is_question():
    out = forecast_track._infer_outcomes_when_missing({
        "title": "Is BTC above $200k on Jan 1 2027?",
    })
    assert out == ["Yes", "No"]


def test_binary_should_question():
    out = forecast_track._infer_outcomes_when_missing({
        "title": "Should the SEC approve the spot Solana ETF in 2026?",
    })
    assert out == ["Yes", "No"]


def test_binary_can_question():
    out = forecast_track._infer_outcomes_when_missing({
        "title": "Can OpenAI ship GPT-6 before end of 2026?",
    })
    assert out == ["Yes", "No"]


def test_binary_heuristic_does_not_call_llm():
    """Binary detection should be local + cheap, no API call."""
    with patch.object(forecast_track, "_aclient") as mock_client:
        out = forecast_track._infer_outcomes_when_missing({
            "title": "Will it rain tomorrow?",
        })
        assert out == ["Yes", "No"]
        mock_client.assert_not_called()


# -- Non-binary inference (Haiku) ----------------------------------------


def test_haiku_inference_for_multi_outcome():
    """Title not a yes/no question -> fire Haiku, return its array."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='["Democrat", "Republican", "Third party"]')]
    with patch.object(forecast_track, "_aclient") as mock_client:
        mock_client.return_value.messages.create.return_value = fake_response
        out = forecast_track._infer_outcomes_when_missing({
            "title": "Which party wins the 2028 US election?",
            "category": "Politics",
        })
    assert out == ["Democrat", "Republican", "Third party"]


def test_haiku_inference_with_prose_around_array():
    """Haiku sometimes wraps the array in prose; the parser should still
    extract just the JSON list."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='Here you go: ["Yes", "No", "Maybe"] - hope that helps.')]
    with patch.object(forecast_track, "_aclient") as mock_client:
        mock_client.return_value.messages.create.return_value = fake_response
        out = forecast_track._infer_outcomes_when_missing({
            "title": "How likely is X by Y?",
            "category": "Tech",
        })
    assert out == ["Yes", "No", "Maybe"]


def test_haiku_failure_falls_back_to_binary():
    """When Haiku errors or returns garbage, default to Yes/No rather than
    explode -- something is always better than empty probabilities."""
    with patch.object(forecast_track, "_aclient") as mock_client:
        mock_client.return_value.messages.create.side_effect = RuntimeError("API down")
        out = forecast_track._infer_outcomes_when_missing({
            "title": "How likely is X by Y?",
        })
    assert out == ["Yes", "No"]


def test_haiku_returns_too_many_outcomes_rejected():
    """If Haiku returns >10 outcomes, treat as garbage and fall back."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='[' + ",".join(f'"o{i}"' for i in range(20)) + ']')]
    with patch.object(forecast_track, "_aclient") as mock_client:
        mock_client.return_value.messages.create.return_value = fake_response
        out = forecast_track._infer_outcomes_when_missing({
            "title": "How likely?",
        })
    assert out == ["Yes", "No"]  # fallback


def test_haiku_returns_one_outcome_rejected():
    """Single-outcome lists are nonsense for a prediction market."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='["Just one thing"]')]
    with patch.object(forecast_track, "_aclient") as mock_client:
        mock_client.return_value.messages.create.return_value = fake_response
        out = forecast_track._infer_outcomes_when_missing({
            "title": "How likely?",
        })
    assert out == ["Yes", "No"]  # fallback


# -- Empty title edge case ------------------------------------------------


def test_empty_title_returns_empty():
    """If we have nothing at all to work from, return empty -- the caller
    must handle this case explicitly. Don't silently invent Yes/No."""
    out = forecast_track._infer_outcomes_when_missing({})
    assert out == []
    out = forecast_track._infer_outcomes_when_missing({"title": ""})
    assert out == []
    out = forecast_track._infer_outcomes_when_missing({"title": "   "})
    assert out == []
