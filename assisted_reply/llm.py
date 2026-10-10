"""The Anthropic call: one Messages API request per draft."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from .config import Config

log = logging.getLogger(__name__)


class DraftError(Exception):
    """A draft could not be produced. The message is short, specific and safe to show to the owner."""


def describe_error(exc: anthropic.APIError) -> str:
    """Turn an SDK error into a short message for the owner."""
    if isinstance(exc, anthropic.APITimeoutError):
        return "Claude took too long to answer (timeout). Try again."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Couldn't reach the Anthropic API (network error). Try again."
    if isinstance(exc, anthropic.AuthenticationError):
        return "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "This API key isn't allowed to use that model. Check the workspace and ANTHROPIC_MODEL."
    if isinstance(exc, anthropic.NotFoundError):
        return "The model wasn't found. Check ANTHROPIC_MODEL."
    if isinstance(exc, anthropic.RateLimitError):
        if exc.response.headers.get("retry-after"):
            return "Rate limited by the Anthropic API. Wait a moment and try again."
        return (
            "The Anthropic API refused the request (429): rate limited, or the monthly spend cap of your"
            " usage tier is reached. Check Billing in the Claude Console."
        )
    if isinstance(exc, anthropic.APIStatusError):
        error_type = getattr(exc, "type", None)
        message = str(getattr(exc, "message", "")).lower()
        if exc.status_code == 402 or error_type == "billing_error":
            return "Anthropic billing problem. Check Billing in the Claude Console."
        if "credit balance" in message:
            return "The Anthropic credit balance is too low. Buy credits under Billing in the Claude Console."
        # A spend limit set on the workspace or organization comes back as a 400.
        if "spend limit" in message or "usage limit" in message:
            return "The spend limit for this workspace is reached. Raise it in the Claude Console or wait."
        if exc.status_code == 529 or error_type == "overloaded_error":
            return "The Anthropic API is overloaded right now. Try again in a moment."
        if exc.status_code >= 500:
            return f"The Anthropic API had a server error ({exc.status_code}). Try again."
        return f"The Anthropic API rejected the request ({exc.status_code}). The log has the details."
    return "The draft failed with an unexpected API error. The log has the details."


class DraftClient:
    def __init__(self, config: Config, client: Any | None = None) -> None:
        self._model = config.anthropic_model
        self._effort = config.anthropic_effort
        self._max_tokens = config.anthropic_max_tokens
        self._client = client or anthropic.AsyncAnthropic(
            api_key=config.anthropic_api_key,
            timeout=config.anthropic_timeout,
            max_retries=config.anthropic_max_retries,
        )

    async def close(self) -> None:
        await self._client.close()

    async def draft(self, system: list[dict[str, Any]], user_message: str) -> str:
        """Ask for one reply. Raises DraftError with a message that can be shown to the owner."""
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_message}],
        }
        # Thinking stays on its default (adaptive): not every model accepts turning it off.
        # A low effort is what keeps the draft fast.
        if self._effort:
            params["output_config"] = {"effort": self._effort}

        try:
            response = await self._client.messages.create(**params)
        except anthropic.APIStatusError as exc:
            # API error messages describe the request parameters, never the conversation.
            log.warning(
                "Anthropic API error: status=%s type=%s request_id=%s message=%s",
                exc.status_code,
                getattr(exc, "type", None),
                getattr(exc, "request_id", None),
                getattr(exc, "message", ""),
            )
            raise DraftError(describe_error(exc)) from exc
        except anthropic.APIError as exc:
            log.warning("Anthropic API error: %s", exc.__class__.__name__)
            raise DraftError(describe_error(exc)) from exc

        usage = response.usage
        log.info(
            "Draft usage: model=%s input=%s output=%s cache_read=%s cache_write=%s stop=%s",
            response.model,
            usage.input_tokens,
            usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", None) or 0,
            getattr(usage, "cache_creation_input_tokens", None) or 0,
            response.stop_reason,
        )

        if response.stop_reason == "refusal":
            raise DraftError("The model declined to write a reply to this message.")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            if response.stop_reason == "max_tokens":
                raise DraftError("The model ran out of tokens before writing. Raise ANTHROPIC_MAX_TOKENS.")
            raise DraftError("The model returned an empty reply. Try again.")
        return text
