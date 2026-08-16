import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from storecipe_mcp.errors import IngestionClientError
from storecipe_mcp.models import (
    IngredientNormalizationRequest,
    IngredientNormalizationResponse,
    RecipeCreateIdempotencyKey,
)

DEFAULT_MAX_RESPONSE_BYTES = 2_097_152
MIN_MAX_RESPONSE_BYTES = 65_536
MAX_MAX_RESPONSE_BYTES = 16_777_216
MAX_BEARER_TOKEN_LENGTH = 4_096
MAX_SAFE_RETRY_AFTER_SECONDS = 300
DEFAULT_READINESS_TIMEOUT_SECONDS = 5.0

_WRITE_SCOPE = "recipes:write"
_ALLOWLISTED_CONFLICT_CATEGORIES = frozenset({"idempotency_conflict"})
_IDEMPOTENCY_KEY_ADAPTER = TypeAdapter(RecipeCreateIdempotencyKey)


@dataclass(frozen=True, slots=True)
class _BufferedResponse:
    status_code: int
    headers: httpx.Headers
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


class IngestionClient:
    """Authenticated, single-request-at-a-time boundary to Ingestion REST."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not MIN_MAX_RESPONSE_BYTES <= max_response_bytes <= MAX_MAX_RESPONSE_BYTES
        ):
            raise ValueError("max_response_bytes is outside the allowed bounds")
        self._http = http
        self._max_response_bytes = max_response_bytes

    async def normalize_ingredients(
        self,
        request: IngredientNormalizationRequest,
        idempotency_key: str,
        token: str,
    ) -> IngredientNormalizationResponse:
        token = _require_bearer_token(token)
        idempotency_key = _validate_idempotency_key(idempotency_key)
        response = await self._request(
            "POST",
            "/v1/ingredient-normalizations",
            token=token,
            required_scope=_WRITE_SCOPE,
            json_body=request.model_dump(mode="json", by_alias=True),
            idempotency_key=idempotency_key,
            success_statuses=frozenset({200}),
            invalid_category="invalid_input",
        )
        return _decode_model(response, IngredientNormalizationResponse)

    async def readiness(self) -> Mapping[str, str]:
        timeout = self._http.timeout.read or DEFAULT_READINESS_TIMEOUT_SECONDS
        try:
            async with asyncio.timeout(timeout):
                response = await self._send(
                    "GET",
                    "/health/ready",
                    headers={},
                    required_scope=None,
                    success_statuses=frozenset({200}),
                    invalid_category="temporary_ingestion_failure",
                )
        except TimeoutError:
            raise IngestionClientError("temporary_ingestion_failure", retryable=True) from None
        return _decode_readiness(response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        required_scope: str | None,
        json_body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        success_statuses: frozenset[int],
        invalid_category: str,
    ) -> _BufferedResponse:
        headers = _authenticated_headers(token, json_body, idempotency_key)
        return await self._send(
            method,
            path,
            headers=headers,
            json_body=json_body,
            required_scope=required_scope,
            success_statuses=success_statuses,
            invalid_category=invalid_category,
        )

    async def _send(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
        required_scope: str | None,
        success_statuses: frozenset[int],
        invalid_category: str,
    ) -> _BufferedResponse:
        try:
            async with self._http.stream(
                method,
                path,
                headers=headers,
                json=json_body,
            ) as response:
                body = await _read_response_body(response, self._max_response_bytes)
                buffered = _BufferedResponse(response.status_code, response.headers, body)
        except IngestionClientError:
            raise
        except (TimeoutError, httpx.HTTPError):
            raise IngestionClientError("temporary_ingestion_failure", retryable=True) from None

        if buffered.status_code in success_statuses:
            return buffered
        if 200 <= buffered.status_code < 300:
            raise IngestionClientError("temporary_ingestion_failure", retryable=True)
        raise _map_response_error(
            buffered,
            required_scope=required_scope,
            invalid_category=invalid_category,
        )


def _authenticated_headers(
    token: str,
    json_body: Mapping[str, Any] | None,
    idempotency_key: str | None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _read_response_body(response: httpx.Response, max_response_bytes: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > max_response_bytes:
            raise IngestionClientError("temporary_ingestion_failure", retryable=True)
        body.extend(chunk)
    return bytes(body)


def _require_bearer_token(token: str) -> str:
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= MAX_BEARER_TOKEN_LENGTH
        or not all(0x21 <= ord(character) <= 0x7E for character in token)
    ):
        raise IngestionClientError("authentication_required", retryable=False)
    return token


def _validate_idempotency_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or not all(0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise IngestionClientError("invalid_input", retryable=False)
    try:
        return _IDEMPOTENCY_KEY_ADAPTER.validate_python(value, strict=True)
    except (TypeError, ValueError, ValidationError):
        raise IngestionClientError("invalid_input", retryable=False) from None


def _decode_model[ModelT: BaseModel](
    response: _BufferedResponse, model_type: type[ModelT]
) -> ModelT:
    try:
        return model_type.model_validate(response.json())
    except (TypeError, ValueError, ValidationError):
        raise IngestionClientError("temporary_ingestion_failure", retryable=True) from None


def _decode_readiness(response: _BufferedResponse) -> Mapping[str, str]:
    try:
        body = response.json()
    except (TypeError, ValueError):
        raise IngestionClientError("temporary_ingestion_failure", retryable=True) from None

    if not isinstance(body, Mapping):
        raise IngestionClientError("temporary_ingestion_failure", retryable=True)
    status = body.get("status")
    service = body.get("service")
    dependencies = body.get("dependencies")
    if (
        status != "ok"
        or not isinstance(service, str)
        or not service
        or not isinstance(dependencies, Mapping)
        or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in dependencies.items()
        )
    ):
        raise IngestionClientError("temporary_ingestion_failure", retryable=True)
    return {"ingestion": "ok"}


def _map_response_error(
    response: _BufferedResponse,
    *,
    required_scope: str | None,
    invalid_category: str,
) -> IngestionClientError:
    status = response.status_code
    if status == 401:
        return IngestionClientError("authentication_required", retryable=False)
    if status == 403:
        return IngestionClientError(
            "insufficient_scope",
            retryable=False,
            required_scope=required_scope,
        )
    if status == 409:
        category = _allowlisted_problem_category(response)
        if category is not None:
            return IngestionClientError(category, retryable=False)
        return IngestionClientError("temporary_ingestion_failure", retryable=False)
    if status == 429:
        return IngestionClientError(
            "ingestion_rate_limited",
            retryable=True,
            retry_after=_safe_retry_after(response.headers.get("Retry-After")),
        )
    if status == 422:
        return IngestionClientError(invalid_category, retryable=False)
    if status == 502:
        return IngestionClientError("ingredient_normalization_invalid_output", retryable=False)
    if 400 <= status < 500:
        return IngestionClientError("temporary_ingestion_failure", retryable=False)
    return IngestionClientError(
        "temporary_ingestion_failure",
        retryable=500 <= status < 600,
    )


def _allowlisted_problem_category(response: _BufferedResponse) -> str | None:
    try:
        body = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(body, Mapping):
        return None
    category = body.get("errorCategory")
    if isinstance(category, str) and category in _ALLOWLISTED_CONFLICT_CATEGORIES:
        return category
    return None


def _safe_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value.isascii() or not value.isdecimal():
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= seconds <= MAX_SAFE_RETRY_AFTER_SECONDS:
        return seconds
    return None
