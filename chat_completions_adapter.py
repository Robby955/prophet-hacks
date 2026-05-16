"""OpenAI-compatible /v1/chat/completions adapter.

Built for Prophet Arena's general onboarding form at
<https://prophetarena.co/onboarding>, which expects an OpenAI-shape
chat-completions endpoint. PA appends /chat/completions to a submitted
base URL and POSTs the standard OpenAI message body. Our adapter
translates that into an Anthropic Messages API call against Claude
Opus 4.7, then translates the response back into OpenAI shape.

Kept as a standalone FastAPI router so it can be mounted via one line
in forecast_agent_server.py and reasoned about independently. The live
forecasting variant (/predict) is untouched.

Conformance:
  - POST /v1/chat/completions
    {model, messages, temperature?, max_tokens?, top_p?, stream?, ...}
  - Response: {id, object, created, model, choices[].message, usage}
  - Reasonable subset of the spec; not all parameters honored. Stream
    is intentionally not supported (returns 400) — adding it is
    straightforward but out of scope for the hackathon submission.

Auth: takes the standard `Authorization: Bearer <key>` header and
compares constant-time to PROPHET_CHAT_API_KEY if set. If unset the
endpoint is open (matches local-dev behavior of the rest of the app).
"""

from __future__ import annotations

import logging
import os
import secrets
import time
import uuid
from typing import Any, Optional

from anthropic import Anthropic
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("oracles.chat_shim")
router = APIRouter()


# -- Schemas ---------------------------------------------------------------


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI ChatCompletion request (subset).

    extra='allow' so PA can send fields we don't honor (e.g. top_p,
    presence_penalty, frequency_penalty, response_format) without
    breaking us.
    """
    model_config = ConfigDict(extra="allow")
    model: str
    messages: list[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = Field(default=None, ge=1, le=8192)
    max_completion_tokens: Optional[int] = Field(default=None, ge=1, le=8192)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    stream: Optional[bool] = False
    n: Optional[int] = Field(default=None, ge=1)


class ChoiceMessage(BaseModel):
    role: str
    content: str


class Choice(BaseModel):
    index: int
    message: ChoiceMessage
    finish_reason: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: list[Choice]
    usage: Usage


# -- Adapter helpers -------------------------------------------------------


def _expected_api_key() -> str:
    """Optional bearer token. If unset, /v1/chat/completions is open
    (useful for local dev). In production we recommend setting it before
    PA exercises the endpoint."""
    return os.environ.get("PROPHET_CHAT_API_KEY", "").strip()


def _client() -> Anthropic:
    """Lazy-init Anthropic client. Pulls ANTHROPIC_API_KEY from env."""
    return Anthropic()


# Reasonable upstream model mapping. PA will probably send 'gpt-4o' or
# similar OpenAI model names; we route every request to Opus 4.7 (the
# production forecasting model) so the underlying capability is what
# we tested. We surface the requested model name in the response so PA
# can correlate.
_UPSTREAM_MODEL = os.environ.get(
    "PROPHET_CHAT_UPSTREAM_MODEL", "claude-opus-4-7",
)


def _split_system_and_user(messages: list[ChatMessage]) -> tuple[str, list[dict[str, str]]]:
    """Anthropic separates `system` from the messages list; OpenAI puts
    system as the first message with role='system'. Split + flatten.

    For multimodal content (list-of-blocks), only text blocks are kept;
    images aren't supported in this shim.
    """
    sys_chunks: list[str] = []
    other: list[dict[str, str]] = []
    for m in messages:
        text = m.content
        if isinstance(text, list):
            text = "".join(
                b.get("text", "")
                for b in text
                if isinstance(b, dict) and b.get("type") == "text"
            )
        text = str(text or "")
        if m.role == "system":
            sys_chunks.append(text)
        elif m.role in ("user", "assistant"):
            other.append({"role": m.role, "content": text})
        # tool messages quietly dropped; we don't expose tools.
    system = "\n\n".join(s for s in sys_chunks if s).strip()
    return system, other


# -- Route -----------------------------------------------------------------


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
def chat_completions(
    body: ChatCompletionRequest,
    authorization: Optional[str] = Header(default=None),
) -> ChatCompletionResponse:
    """OpenAI-compatible chat completions endpoint backed by Anthropic
    Opus 4.7. Built for PA's onboarding integration; not used by the
    /predict forecasting path."""
    # Auth
    expected = _expected_api_key()
    if expected:
        sent = ""
        if authorization and authorization.lower().startswith("bearer "):
            sent = authorization[7:].strip()
        if not sent or not secrets.compare_digest(sent, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid api key",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="streaming not supported in this adapter",
        )
    if body.n and body.n != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only n=1 supported",
        )

    system, messages = _split_system_and_user(body.messages)
    if not messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one non-system message required",
        )

    # Anthropic max_tokens is required. Pick the most reasonable cap we
    # can from the request, default to 1024.
    max_tokens = body.max_completion_tokens or body.max_tokens or 1024

    # Anthropic API call
    t0 = time.time()
    try:
        kwargs: dict[str, Any] = {
            "model": _UPSTREAM_MODEL,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        # Opus 4.7 rejects custom temperature; only pass if non-default
        # AND model isn't Opus 4.7 (per docs note).
        if body.temperature is not None and "opus-4-7" not in _UPSTREAM_MODEL.lower():
            kwargs["temperature"] = body.temperature
        if body.top_p is not None and "opus-4-7" not in _UPSTREAM_MODEL.lower():
            kwargs["top_p"] = body.top_p

        resp = _client().messages.create(**kwargs)
    except Exception as e:
        log.warning("chat shim upstream call failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"upstream error: {type(e).__name__}",
        ) from e

    # Extract text content. Anthropic returns content as a list of
    # blocks; we concatenate any text blocks.
    out_text = "".join(
        getattr(b, "text", "") for b in (resp.content or [])
        if getattr(b, "type", "") == "text"
    )

    # Map Anthropic stop_reason -> OpenAI finish_reason
    stop_reason = getattr(resp, "stop_reason", None) or ""
    if stop_reason == "end_turn":
        finish = "stop"
    elif stop_reason == "max_tokens":
        finish = "length"
    elif stop_reason == "stop_sequence":
        finish = "stop"
    else:
        finish = "stop"

    usage = getattr(resp, "usage", None)
    p_tok = getattr(usage, "input_tokens", 0) if usage else 0
    c_tok = getattr(usage, "output_tokens", 0) if usage else 0

    latency_ms = int((time.time() - t0) * 1000)
    log.info(
        "chat shim %s -> %s: in=%d out=%d %dms",
        body.model, _UPSTREAM_MODEL, p_tok, c_tok, latency_ms,
    )

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
        object="chat.completion",
        created=int(time.time()),
        # Echo the model name the client asked for; PA may compare it.
        model=body.model,
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(role="assistant", content=out_text),
                finish_reason=finish,
            ),
        ],
        usage=Usage(
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=p_tok + c_tok,
        ),
    )


@router.get("/v1/models", include_in_schema=False)
def list_models() -> dict[str, Any]:
    """OpenAI-compatible model list. Some clients probe this before
    using /chat/completions. We expose one model that maps to our
    upstream."""
    return {
        "object": "list",
        "data": [
            {
                "id": _UPSTREAM_MODEL,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "forecastingpath",
            },
        ],
    }
