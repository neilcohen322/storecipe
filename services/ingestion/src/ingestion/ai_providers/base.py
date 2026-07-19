"""Shared semantic contracts for AI provider adapters."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field


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


class AiUsage(BaseModel):
    """Normalized usage returned by a provider adapter."""

    model_config = ConfigDict(extra="ignore")

    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    cost: Annotated[Decimal, Field(ge=0)] = Decimal(0)


@dataclass(frozen=True, slots=True)
class AiRequest:
    """Semantic structured-output request translated by each provider adapter."""

    system_instructions: str
    user_content: str
    output_schema_name: str
    output_schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class AiCompletion:
    """Normalized provider completion consumed by application workflows."""

    content: str
    model: str
    finish_reason: str
    usage: AiUsage


class AiProvider(Protocol):
    """Contract implemented by every provider adapter."""

    async def complete(self, *, request: AiRequest) -> AiCompletion: ...
