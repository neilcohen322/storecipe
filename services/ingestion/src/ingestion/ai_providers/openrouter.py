"""OpenRouter implementation of the AI provider contract."""

import aiohttp
from pydantic import BaseModel, ConfigDict, ValidationError

from ingestion.ai_providers.base import (
    AiCompletion,
    AiExtractionError,
    AiExtractionFailureCode,
    AiRequest,
    AiUsage,
)
from ingestion.ai_providers.config import AiProviderConfig

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-5-nano"


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
    usage: AiUsage


class OpenRouterProvider:
    """Non-streaming OpenRouter adapter with normalized output and failures."""

    def __init__(self, config: AiProviderConfig) -> None:
        self._api_key = config.api_key
        self._model = config.model
        self._endpoint = config.endpoint or OPENROUTER_CHAT_COMPLETIONS_URL
        self._timeout_seconds = config.timeout_seconds
        self._max_output_tokens = config.max_output_tokens

    async def complete(
        self,
        *,
        request: AiRequest,
    ) -> AiCompletion:
        if not self._api_key:
            raise AiExtractionError(AiExtractionFailureCode.NOT_CONFIGURED)

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": request.system_instructions,
                },
                {
                    "role": "user",
                    "content": request.user_content,
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.output_schema_name,
                    "strict": True,
                    "schema": request.output_schema,
                },
            },
            "max_tokens": self._max_output_tokens,
            "reasoning": {
                "effort": "minimal",
                "exclude": True,
            },
            # Prevent OpenRouter from silently routing to a provider that ignores
            # the strict response-format parameter.
            "provider": {"require_parameters": True},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(self._endpoint, headers=headers, json=payload) as response,
            ):
                raw_body = await response.text()
                if response.status == 429:
                    raise AiExtractionError(
                        AiExtractionFailureCode.RATE_LIMITED,
                        status=response.status,
                    )
                if not 200 <= response.status < 300:
                    raise AiExtractionError(
                        AiExtractionFailureCode.PROVIDER_REQUEST_FAILED,
                        status=response.status,
                    )
        except AiExtractionError:
            raise
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise AiExtractionError(AiExtractionFailureCode.PROVIDER_REQUEST_FAILED) from exc

        try:
            envelope = _OpenRouterEnvelope.model_validate_json(raw_body)
            choice = envelope.choices[0]
        except (ValidationError, IndexError) as exc:
            raise AiExtractionError(AiExtractionFailureCode.INVALID_PROVIDER_RESPONSE) from exc

        if choice.finish_reason != "stop" or choice.message.content is None:
            raise AiExtractionError(AiExtractionFailureCode.INVALID_PROVIDER_RESPONSE)
        return AiCompletion(
            content=choice.message.content,
            model=envelope.model,
            finish_reason=choice.finish_reason,
            usage=envelope.usage,
        )
