"""Tests for the OpenAI-compatible /v1/chat/completions shim.

Used by Prophet Arena's general onboarding form at
<https://prophetarena.co/onboarding>. Tests cover request shape,
response shape, system-message splitting, auth, and error handling.
The Anthropic upstream is mocked so tests are fast + deterministic.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """Server with the chat shim mounted and auth disabled."""
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    monkeypatch.delenv("PROPHET_CHAT_API_KEY", raising=False)
    import forecast_agent_server as server
    importlib.reload(server)
    return TestClient(server.app)


def _fake_response(text: str = "ok", input_tokens: int = 12, output_tokens: int = 3):
    """Build a mock Anthropic Messages API response."""
    msg = MagicMock()
    msg.type = "text"
    msg.text = text
    resp = MagicMock()
    resp.content = [msg]
    resp.stop_reason = "end_turn"
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    resp.usage = usage
    return resp


# -- Happy path ------------------------------------------------------------


def test_chat_completion_basic_shape(client):
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response("hello world")
        r = client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        })
    assert r.status_code == 200
    body = r.json()
    for key in ("id", "object", "created", "model", "choices", "usage"):
        assert key in body, f"missing {key!r}"
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-4o"  # echoes what client asked for
    assert body["choices"][0]["message"]["content"] == "hello world"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 15


def test_chat_completion_routes_to_upstream_opus(client):
    """The shim should ALWAYS call Opus 4.7 regardless of the requested
    model name. PA might send 'gpt-4o' or whatever; we route to our
    production-tested model."""
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response()
        client.post("/v1/chat/completions", json={
            "model": "gpt-4o-2024-08-06",
            "messages": [{"role": "user", "content": "x"}],
        })
        call = fake_client.return_value.messages.create.call_args
        assert "opus-4-7" in call.kwargs["model"]


def test_system_message_split_correctly(client):
    """OpenAI puts system in messages; Anthropic separates it. The
    shim should pull system messages out of the message list and pass
    them to Anthropic's `system` parameter."""
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response()
        client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "you are calibrated"},
                {"role": "user", "content": "forecast event X"},
            ],
        })
        call = fake_client.return_value.messages.create.call_args
        assert call.kwargs.get("system") == "you are calibrated"
        assert call.kwargs["messages"] == [
            {"role": "user", "content": "forecast event X"},
        ]


def test_multiple_system_messages_concatenated(client):
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response()
        client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "first system"},
                {"role": "system", "content": "second system"},
                {"role": "user", "content": "hello"},
            ],
        })
        call = fake_client.return_value.messages.create.call_args
        assert "first system" in call.kwargs["system"]
        assert "second system" in call.kwargs["system"]


def test_max_tokens_honored(client):
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response()
        client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 500,
        })
        call = fake_client.return_value.messages.create.call_args
        assert call.kwargs["max_tokens"] == 500


def test_max_completion_tokens_preferred_when_both_set(client):
    """OpenAI's newer field max_completion_tokens should win."""
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response()
        client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
            "max_completion_tokens": 700,
        })
        call = fake_client.return_value.messages.create.call_args
        assert call.kwargs["max_tokens"] == 700


def test_temperature_dropped_for_opus_4_7(client):
    """Opus 4.7 rejects custom temperature; the shim must omit it."""
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response()
        client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "x"}],
            "temperature": 0.7,
        })
        call = fake_client.return_value.messages.create.call_args
        assert "temperature" not in call.kwargs


def test_extra_unknown_fields_accepted(client):
    """OpenAI clients send fields we don't honor (top_p,
    presence_penalty, response_format, etc.); shouldn't reject them."""
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response()
        r = client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "x"}],
            "presence_penalty": 0.5,
            "frequency_penalty": 0.2,
            "response_format": {"type": "text"},
            "seed": 42,
        })
    assert r.status_code == 200


# -- Errors ----------------------------------------------------------------


def test_stream_not_supported(client):
    r = client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
        "stream": True,
    })
    assert r.status_code == 400
    assert "stream" in r.json()["detail"].lower()


def test_n_greater_than_one_rejected(client):
    r = client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
        "n": 3,
    })
    assert r.status_code == 400


def test_no_non_system_messages_rejected(client):
    r = client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "system", "content": "x"}],
    })
    assert r.status_code == 400


def test_required_field_missing(client):
    r = client.post("/v1/chat/completions", json={"messages": []})
    assert r.status_code == 422


def test_upstream_error_returns_502(client):
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.side_effect = RuntimeError("rate limited")
        r = client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "x"}],
        })
    assert r.status_code == 502


# -- Auth ------------------------------------------------------------------


def test_auth_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("PROPHET_CHAT_API_KEY", "expected-secret")
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    import forecast_agent_server as server
    importlib.reload(server)
    c = TestClient(server.app)

    # Missing key
    r = c.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    })
    assert r.status_code == 401

    # Wrong key
    r = c.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert r.status_code == 401

    # Right key passes
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response()
        r = c.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]},
            headers={"Authorization": "Bearer expected-secret"},
        )
    assert r.status_code == 200


# -- /v1/models ------------------------------------------------------------


def test_v1_models_lists_one_model(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    assert "claude-opus" in body["data"][0]["id"].lower()


# -- Multimodal content gracefully degraded --------------------------------


def test_block_content_text_blocks_extracted(client):
    """OpenAI supports content as a list of {type: 'text', text: ...}
    blocks for multimodal. We pull out just the text parts."""
    with patch("chat_completions_adapter._client") as fake_client:
        fake_client.return_value.messages.create.return_value = _fake_response()
        client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello "},
                    {"type": "text", "text": "world"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
                ],
            }],
        })
        call = fake_client.return_value.messages.create.call_args
        assert call.kwargs["messages"][0]["content"] == "hello world"
