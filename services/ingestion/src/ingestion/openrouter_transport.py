"""Shared OpenRouter transport types and aiohttp client."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Protocol

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, ValidationError

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-5.6-luna"
REQUEST_TIMEOUT_SECONDS = 30.0

SerializeRequest = Callable[..., bytes]


class OpenRouterUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    cost: Annotated[Decimal, Field(ge=0)] = Decimal(0)


@dataclass(frozen=True, slots=True)
class OpenRouterCompletion:
    content: str
    model: str
    finish_reason: str
    usage: OpenRouterUsage


class _OpenRouterMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str | None


class _OpenRouterChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: _OpenRouterMessage
    finish_reason: str | None = None


class _OpenRouterEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    choices: list[_OpenRouterChoice]
    usage: OpenRouterUsage


class OpenRouterErrorMapper(Protocol):
    def not_configured(self) -> Exception: ...

    def rate_limited(self, status: int) -> Exception: ...

    def provider_failed(self, status: int | None, *, started: bool) -> Exception: ...

    def invalid_response(self) -> Exception: ...

    def timeout(self) -> Exception: ...


class AiohttpOpenRouterTransport:
    """Non-streaming OpenRouter transport parameterized by serializer and error mapping."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_OPENROUTER_MODEL,
        endpoint: str = OPENROUTER_CHAT_COMPLETIONS_URL,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        serialize_request: SerializeRequest,
        error_mapper: OpenRouterErrorMapper,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        self._serialize_request = serialize_request
        self._error_mapper = error_mapper

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, object],
    ) -> OpenRouterCompletion:
        if not self._api_key:
            raise self._error_mapper.not_configured()

        payload = self._serialize_request(
            model=self._model,
            messages=messages,
            response_format=response_format,
        )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(self._endpoint, headers=headers, data=payload) as response,
            ):
                raw_body = await response.text()
                if response.status == 429:
                    raise self._error_mapper.rate_limited(response.status)
                if not 200 <= response.status < 300:
                    raise self._error_mapper.provider_failed(
                        response.status,
                        started=True,
                    )
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise self._error_mapper.timeout() from exc

        try:
            envelope = _OpenRouterEnvelope.model_validate_json(raw_body)
            choice: _OpenRouterChoice = envelope.choices[0]
        except (ValidationError, IndexError) as exc:
            raise self._error_mapper.invalid_response() from exc

        if choice.finish_reason != "stop" or choice.message.content is None:
            raise self._error_mapper.invalid_response()
        return OpenRouterCompletion(
            content=choice.message.content,
            model=envelope.model,
            finish_reason=choice.finish_reason,
            usage=envelope.usage,
        )
