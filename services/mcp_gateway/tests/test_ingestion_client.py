import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
import pytest

from storecipe_mcp.errors import IngestionClientError
from storecipe_mcp.ingestion_client import IngestionClient
from storecipe_mcp.models import IngredientNormalizationRequest

TOKEN = "verified-raw-token"
SECRET_INGREDIENT = "secret-ingredient-marker"
SECRET_VALUES = (TOKEN, "idem-secret-key", SECRET_INGREDIENT)
Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


def _normalization_request() -> IngredientNormalizationRequest:
    return IngredientNormalizationRequest.model_validate(
        {"ingredients": [{"rawText": SECRET_INGREDIENT}]}
    )


def _normalization_response_payload() -> dict[str, object]:
    return {
        "ingredients": [
            {
                "rawText": SECRET_INGREDIENT,
                "name": "tomato",
                "canonicalName": "tomato",
                "quantity": 2,
                "unit": "piece",
            }
        ]
    }


@asynccontextmanager
async def _ingestion_client(
    handler: Handler, *, max_response_bytes: int | None = None
) -> AsyncIterator[IngestionClient]:
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://ingestion.example",
        follow_redirects=False,
    ) as http:
        if max_response_bytes is None:
            yield IngestionClient(http)
        else:
            yield IngestionClient(http, max_response_bytes=max_response_bytes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [None, "", " ", "\t", "\r\n", "\x00", "token-é", "a" * 4097, 123],
    ids=["none", "empty", "space", "tab", "crlf", "control", "non-ascii", "too-long", "wrong-type"],
)
async def test_normalize_rejects_invalid_tokens_without_a_request(token: object) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid token must be rejected before the request")

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(
                _normalization_request(), "idem-secret-key", cast(Any, token)
            )

    assert captured.value.category == "authentication_required"
    assert captured.value.retryable is False
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "idempotency_key",
    [None, 123, "", "short", "a" * 129, "has space", "slash/value", "\r\n", "clé"],
    ids=["none", "wrong-type", "empty", "short", "too-long", "space", "slash", "crlf", "non-ascii"],
)
async def test_normalize_rejects_invalid_idempotency_keys_without_a_request(
    idempotency_key: object,
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid idempotency key must be rejected before the request")

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(
                _normalization_request(), cast(Any, idempotency_key), TOKEN
            )

    assert captured.value.category == "invalid_input"
    assert captured.value.retryable is False
    assert calls == 0


@pytest.mark.asyncio
async def test_normalize_ingredients_sends_exact_json_and_idempotency_key() -> None:
    seen: list[httpx.Request] = []
    request = _normalization_request()
    key = "idem-secret-key"

    async def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append(http_request)
        return httpx.Response(200, json=_normalization_response_payload())

    async with _ingestion_client(handler) as client:
        result = await client.normalize_ingredients(request, key, TOKEN)

    assert result.ingredients[0].canonical_name == "tomato"
    assert len(seen) == 1
    http_request = seen[0]
    assert http_request.method == "POST"
    assert http_request.url.path == "/v1/ingredient-normalizations"
    assert http_request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert http_request.headers["Idempotency-Key"] == key
    assert http_request.headers["Content-Type"] == "application/json"
    assert json.loads(http_request.content) == {"ingredients": [{"rawText": SECRET_INGREDIENT}]}
    assert SECRET_INGREDIENT not in str(http_request.headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_category", "expected_scope"),
    [
        (401, "authentication_required", None),
        (403, "insufficient_scope", "recipes:write"),
    ],
)
async def test_authentication_errors_are_typed_without_upstream_details(
    status: int, expected_category: str, expected_scope: str | None
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "detail": f"rawText={SECRET_INGREDIENT}",
                "request_id": "internal-request-id",
                "errorCategory": "unknown_internal_category",
            },
        )

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    error = captured.value
    assert error.category == expected_category
    assert error.retryable is False
    assert error.required_scope == expected_scope
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_idempotency_conflict_is_preserved_without_body_fields() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "errorCategory": "idempotency_conflict",
                "detail": f"rawText={SECRET_INGREDIENT}",
            },
        )

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    error = captured.value
    assert error.category == "idempotency_conflict"
    assert error.retryable is False
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_ingestion_problem_document_409_maps_to_idempotency_conflict() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            headers={"content-type": "application/problem+json"},
            json={
                "type": "https://docs.storecipe.example/problems/idempotency_conflict",
                "title": "Conflict",
                "status": 409,
                "detail": "Idempotency key is already used for a different request.",
                "errorCategory": "idempotency_conflict",
            },
        )

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    assert captured.value.category == "idempotency_conflict"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_409_without_error_category_is_temporary_failure() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Conflict"})

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    assert captured.value.category == "temporary_ingestion_failure"


@pytest.mark.asyncio
async def test_validation_error_is_safe() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": f"rawText={SECRET_INGREDIENT}",
                "errors": [{"field": "ingredients", "message": SECRET_INGREDIENT}],
            },
        )

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    error = captured.value
    assert error.category == "invalid_input"
    assert error.retryable is False
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "expected_retry_after"),
    [
        ("30", 30),
        ("999999", None),
        ("0", None),
        ("not-a-duration", None),
        ("+30", None),
        ("٣٠".encode(), None),
    ],
)
async def test_rate_limit_maps_only_a_bounded_retry_after(
    header: str | bytes, expected_retry_after: int | None
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": header},
            json={"detail": f"rawText={SECRET_INGREDIENT}"},
        )

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    error = captured.value
    assert error.category == "ingestion_rate_limited"
    assert error.retryable is True
    assert error.retry_after == expected_retry_after
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_invalid_output_maps_to_safe_category() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text=f"rawText={SECRET_INGREDIENT}")

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    error = captured.value
    assert error.category == "ingredient_normalization_invalid_output"
    assert error.retryable is False
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_server_error_is_retryable_and_makes_one_upstream_call() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text=f"rawText={SECRET_INGREDIENT}")

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    assert captured.value.category == "temporary_ingestion_failure"
    assert captured.value.retryable is True
    assert calls == 1
    assert all(secret not in str(captured.value) for secret in SECRET_VALUES)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 405, 418])
async def test_unlisted_client_errors_use_the_safe_nonretryable_fallback(status: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "errorCategory": "internal-secret-category",
                "detail": f"rawText={SECRET_INGREDIENT}",
            },
        )

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    error = captured.value
    assert error.category == "temporary_ingestion_failure"
    assert error.retryable is False
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_unknown_conflict_category_uses_the_safe_nonretryable_fallback() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "errorCategory": "internal-secret-conflict",
                "detail": f"rawText={SECRET_INGREDIENT}",
            },
        )

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    error = captured.value
    assert error.category == "temporary_ingestion_failure"
    assert error.retryable is False
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"not": "normalized"}),
    ],
)
async def test_malformed_success_responses_are_safe_temporary_errors(
    response: httpx.Response,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return response

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.normalize_ingredients(_normalization_request(), "idem-secret-key", TOKEN)

    error = captured.value
    assert error.category == "temporary_ingestion_failure"
    assert error.retryable is True
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_readiness_validates_ingestion_health_shape_and_returns_safe_status() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "service": "ingestion",
                "dependencies": {"postgres": "ok", "redis": "ok"},
                "unknown": "secret-body-value",
            },
        )

    async with _ingestion_client(handler) as client:
        result = await client.readiness()

    assert result == {"ingestion": "ok"}
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/health/ready"
    assert "Authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_malformed_readiness_is_a_safe_temporary_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "dependencies": "secret"})

    async with _ingestion_client(handler) as client:
        with pytest.raises(IngestionClientError) as captured:
            await client.readiness()

    assert captured.value.category == "temporary_ingestion_failure"
    assert captured.value.retryable is True
    assert all(secret not in str(captured.value) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_readiness_bounds_a_hanging_transport() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    timeout = httpx.Timeout(connect=1, read=0.01, write=1, pool=1)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://ingestion.example",
        timeout=timeout,
    ) as http:
        client = IngestionClient(http)
        async with asyncio.timeout(0.5):
            with pytest.raises(IngestionClientError) as captured:
                await client.readiness()

    assert captured.value.category == "temporary_ingestion_failure"
    assert captured.value.retryable is True
