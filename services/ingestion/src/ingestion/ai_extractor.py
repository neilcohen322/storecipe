"""Week 5 exercise scaffold: validated recipe extraction through OpenRouter.

Provider transport and orchestration are complete. The three public helper functions
that raise ``Week5ExerciseIncomplete`` are intentionally left for the learner.
"""

import time
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Protocol

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ingestion.import_models import MAX_PG_INT, RecipeImportCandidate

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-5-nano"
PROMPT_VERSION = "week5-exercise-v1"
MAX_OUTPUT_TOKENS = 1_200
REQUEST_TIMEOUT_SECONDS = 30.0


class LlmBoundaryModel(BaseModel):
    """Strict model for data crossing the untrusted LLM boundary."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


NonEmptyText = Annotated[str, Field(min_length=1)]


class LlmIngredientFields(LlmBoundaryModel):
    raw_text: NonEmptyText
    name: Annotated[str, Field(min_length=1, max_length=200)]
    quantity: Annotated[float | None, Field(ge=0)]
    unit: Annotated[str | None, Field(min_length=1, max_length=64)]


class LlmRecipeFields(LlmBoundaryModel):
    """Fields the model may produce.

    ``source_url`` is intentionally absent. Network provenance belongs to the
    application and must never be accepted from model output.
    """

    title: Annotated[str, Field(min_length=1, max_length=200)]
    servings: Annotated[int | None, Field(ge=1, le=MAX_PG_INT)]
    prep_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)]
    cook_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)]
    total_minutes: Annotated[int | None, Field(ge=0, le=MAX_PG_INT)]
    ingredients: Annotated[list[LlmIngredientFields], Field(min_length=1)]
    instructions: Annotated[list[NonEmptyText], Field(min_length=1)]
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]]


class AiExtractionFailureCode(StrEnum):
    NOT_CONFIGURED = "not_configured"
    PROVIDER_REQUEST_FAILED = "provider_request_failed"
    RATE_LIMITED = "rate_limited"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"


class AiExtractionError(Exception):
    """Safe typed failure; raw recipe/model content is deliberately not exposed."""

    def __init__(
        self,
        code: AiExtractionFailureCode,
        *,
        status: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.status = status


class Week5ExerciseIncomplete(RuntimeError):
    """Raised only by the three learner-owned exercise functions."""


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


@dataclass(frozen=True, slots=True)
class AiExtractionResult:
    candidate: RecipeImportCandidate
    model: str
    prompt_version: str
    usage: OpenRouterUsage
    latency_ms: int


class OpenRouterTransport(Protocol):
    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, object],
    ) -> OpenRouterCompletion: ...


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


class AiohttpOpenRouterTransport:
    """Small non-streaming OpenRouter transport for the exercise."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_OPENROUTER_MODEL,
        endpoint: str = OPENROUTER_CHAT_COMPLETIONS_URL,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, object],
    ) -> OpenRouterCompletion:
        if not self._api_key:
            raise AiExtractionError(AiExtractionFailureCode.NOT_CONFIGURED)

        payload = {
            "model": self._model,
            "messages": messages,
            "response_format": response_format,
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
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
        return OpenRouterCompletion(
            content=choice.message.content,
            model=envelope.model,
            finish_reason=choice.finish_reason,
            usage=envelope.usage,
        )


def build_response_format() -> dict[str, object]:
    """Exercise step 1: return OpenRouter's strict JSON Schema response format."""

    raise Week5ExerciseIncomplete(
        "Exercise step 1: implement build_response_format() as described in "
        "project-notes/WEEK_5_EXERCISE.md"
    )


def build_extraction_messages(source_text: str) -> list[dict[str, str]]:
    """Exercise step 2: build the system/user messages for untrusted recipe text."""

    raise Week5ExerciseIncomplete(
        "Exercise step 2: implement build_extraction_messages() as described in "
        "project-notes/WEEK_5_EXERCISE.md"
    )


def candidate_from_model_content(
    content: str,
    *,
    trusted_source_url: str,
) -> RecipeImportCandidate:
    """Exercise step 3: validate model content and attach trusted provenance."""

    raise Week5ExerciseIncomplete(
        "Exercise step 3: implement candidate_from_model_content() as described in "
        "project-notes/WEEK_5_EXERCISE.md"
    )


class AiRecipeExtractor:
    """Compose prompting, provider I/O, validation, and usage telemetry."""

    def __init__(self, transport: OpenRouterTransport) -> None:
        self._transport = transport

    async def extract(
        self,
        *,
        source_text: str,
        trusted_source_url: str,
    ) -> AiExtractionResult:
        messages = build_extraction_messages(source_text)
        response_format = build_response_format()
        started = time.perf_counter()
        completion = await self._transport.complete(
            messages=messages,
            response_format=response_format,
        )
        latency_ms = round((time.perf_counter() - started) * 1_000)
        candidate = candidate_from_model_content(
            completion.content,
            trusted_source_url=trusted_source_url,
        )
        return AiExtractionResult(
            candidate=candidate,
            model=completion.model,
            prompt_version=PROMPT_VERSION,
            usage=completion.usage,
            latency_ms=latency_ms,
        )
