import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx2
import pytest

from assisted_reply.llm import DraftClient, DraftError, describe_error

from .conftest import make_config

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def response(text="sure thing", stop_reason="end_turn", blocks=None):
    if blocks is None:
        blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    usage = SimpleNamespace(
        input_tokens=120, output_tokens=30, cache_read_input_tokens=900, cache_creation_input_tokens=0
    )
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=usage, model="claude-haiku-5-5")


def client_returning(value=None, error=None):
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=value, side_effect=error)
    return client


def status_error(cls, status, error_type, message, headers=None):
    http_response = httpx2.Response(status, request=REQUEST, headers=headers or {})
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    return cls(message, response=http_response, body=body)


async def test_request_shape_and_text_blocks_only(tmp_path, caplog):
    client = client_returning(response("  sure thing  "))
    llm = DraftClient(make_config(tmp_path, ANTHROPIC_MODEL="claude-sonnet-5-5"), client)
    system = [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    with caplog.at_level(logging.INFO):
        assert await llm.draft(system, "USER PART") == "sure thing"

    kwargs = client.messages.create.await_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["system"] == system
    assert kwargs["messages"] == [{"role": "user", "content": "USER PART"}]
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["max_tokens"] == 2048
    assert "temperature" not in kwargs and "thinking" not in kwargs
    # Usage numbers are logged, content is not.
    assert "input=120" in caplog.text and "cache_read=900" in caplog.text
    assert "sure thing" not in caplog.text and "USER PART" not in caplog.text


async def test_effort_can_be_left_out(tmp_path):
    client = client_returning(response())
    await DraftClient(make_config(tmp_path, ANTHROPIC_EFFORT=""), client).draft([], "x")
    assert "output_config" not in client.messages.create.await_args.kwargs


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (response(stop_reason="refusal", blocks=[]), "declined"),
        (response(stop_reason="max_tokens", blocks=[]), "ANTHROPIC_MAX_TOKENS"),
        (response(text="   "), "empty"),
    ],
)
async def test_unusable_responses_raise(tmp_path, reply, expected):
    llm = DraftClient(make_config(tmp_path), client_returning(reply))
    with pytest.raises(DraftError, match=expected):
        await llm.draft([], "x")


ERRORS = [
    (anthropic.APITimeoutError(request=REQUEST), "timeout"),
    (anthropic.APIConnectionError(request=REQUEST), "network"),
    (
        status_error(anthropic.AuthenticationError, 401, "authentication_error", "invalid x-api-key"),
        "API key",
    ),
    (status_error(anthropic.NotFoundError, 404, "not_found_error", "model: nope"), "ANTHROPIC_MODEL"),
    (
        status_error(anthropic.RateLimitError, 429, "rate_limit_error", "slow down", {"retry-after": "5"}),
        "Rate limited",
    ),
    (status_error(anthropic.RateLimitError, 429, "rate_limit_error", "cap"), "spend cap"),
    (
        status_error(
            anthropic.BadRequestError,
            400,
            "invalid_request_error",
            "Your credit balance is too low to access",
        ),
        "credit balance is too low",
    ),
    (
        status_error(
            anthropic.BadRequestError, 400, "invalid_request_error", "workspace spend limit reached"
        ),
        "spend limit",
    ),
    (status_error(anthropic.APIStatusError, 402, "billing_error", "payment"), "billing"),
    (status_error(anthropic.InternalServerError, 529, "overloaded_error", "Overloaded"), "overloaded"),
    (status_error(anthropic.InternalServerError, 500, "api_error", "oops"), "server error"),
    (
        status_error(anthropic.BadRequestError, 400, "invalid_request_error", "bad param"),
        "rejected the request",
    ),
]


@pytest.mark.parametrize(("error", "expected"), ERRORS)
async def test_api_errors_become_short_messages(tmp_path, error, expected):
    assert expected in describe_error(error)
    llm = DraftClient(make_config(tmp_path), client_returning(error=error))
    with pytest.raises(DraftError, match=expected):
        await llm.draft([], "x")


async def test_error_log_has_no_key_or_content(tmp_path, caplog):
    error = status_error(anthropic.AuthenticationError, 401, "authentication_error", "invalid x-api-key")
    llm = DraftClient(make_config(tmp_path), client_returning(error=error))
    with caplog.at_level(logging.DEBUG), pytest.raises(DraftError):
        await llm.draft([{"type": "text", "text": "SYSTEM"}], "PRIVATE MESSAGE TEXT")
    assert "status=401" in caplog.text
    assert "test-key" not in caplog.text
    assert "PRIVATE MESSAGE TEXT" not in caplog.text
