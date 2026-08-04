import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

import httpx
import pytest

from storecipe_mcp.catalog_client import CatalogClient
from storecipe_mcp.errors import CatalogClientError
from storecipe_mcp.models import RecipeCreate, RecipeQueryRequest

TOKEN = "verified-raw-token"
RECIPE_ID = UUID("95da0a55-128e-43c2-bd21-4ef1ec8198fa")
SECRET_VALUES = (TOKEN, "idem-secret-key", "subject-secret", "https://secret.example")
Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


def _recipe_create() -> RecipeCreate:
    return RecipeCreate.model_validate(
        {
            "title": "Tomato soup",
            "sourceUrl": "https://example.com/tomato-soup",
            "servings": 4,
            "prepMinutes": 10,
            "cookMinutes": 20,
            "totalMinutes": 30,
            "ingredients": [
                {"rawText": "2 tomatoes", "name": "tomato", "quantity": 2, "unit": "piece"}
            ],
            "instructions": ["Chop the tomatoes", "Simmer until soft"],
            "tags": ["soup"],
        }
    )


def _recipe_view_payload() -> dict[str, object]:
    return {
        "id": str(RECIPE_ID),
        "title": "Tomato soup",
        "sourceUrl": "https://example.com/tomato-soup",
        "servings": 4,
        "prepMinutes": 10,
        "cookMinutes": 20,
        "totalMinutes": 30,
        "ingredients": [
            {"rawText": "2 tomatoes", "name": "tomato", "quantity": 2, "unit": "piece"}
        ],
        "instructions": ["Chop the tomatoes", "Simmer until soft"],
        "tags": ["soup"],
        "rating": 5,
    }


def _query_page_payload() -> dict[str, object]:
    return {"items": [{"recipe": _recipe_view_payload(), "match": None}], "nextCursor": None}


@asynccontextmanager
async def _catalog_client(
    handler: Handler, *, max_response_bytes: int | None = None
) -> AsyncIterator[CatalogClient]:
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://catalog.example",
        follow_redirects=False,
    ) as http:
        if max_response_bytes is None:
            yield CatalogClient(http)
        else:
            yield CatalogClient(http, max_response_bytes=max_response_bytes)


async def _call_user_method(client: CatalogClient, method: str, token: object) -> object:
    raw_token = cast(Any, token)
    if method == "query":
        return await client.query_recipes(RecipeQueryRequest(), raw_token)
    if method == "get":
        return await client.get_recipe(RECIPE_ID, raw_token)
    if method == "create":
        return await client.create_recipe(_recipe_create(), "idem-secret-key", raw_token)
    return await client.rate_recipe(RECIPE_ID, 4, raw_token)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["query", "get", "create", "rate"])
@pytest.mark.parametrize(
    "token",
    [None, "", " ", "\t", "\r\n", "\x00", "token-é", "a" * 4097, 123],
    ids=["none", "empty", "space", "tab", "crlf", "control", "non-ascii", "too-long", "wrong-type"],
)
async def test_user_methods_reject_invalid_tokens_without_a_request(
    method: str, token: object
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid token must be rejected before the request")

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await _call_user_method(client, method, token)

    assert captured.value.category == "authentication_required"
    assert captured.value.retryable is False
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "idempotency_key",
    [None, 123, "", "short", "a" * 129, "has space", "slash/value", "\r\n", "clé"],
    ids=["none", "wrong-type", "empty", "short", "too-long", "space", "slash", "crlf", "non-ascii"],
)
async def test_create_rejects_invalid_idempotency_keys_without_a_request(
    idempotency_key: object,
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid idempotency key must be rejected before the request")

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.create_recipe(_recipe_create(), cast(Any, idempotency_key), TOKEN)

    assert captured.value.category == "invalid_input"
    assert captured.value.retryable is False
    assert calls == 0


@pytest.mark.asyncio
async def test_query_recipes_serializes_exact_ordered_repeated_query_tuples() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_query_page_payload())

    query = RecipeQueryRequest.model_validate(
        {
            "text": "tomato soup",
            "requiredIngredient": ["tomato", "basil"],
            "availableIngredient": ["water", "salt"],
            "requiredTag": ["dinner", "quick"],
            "preferredTag": ["family", "weeknight"],
            "maxTotalMinutes": 45,
            "minRating": 4,
            "ratingState": "rated",
            "sort": ["rating:desc", "totalMinutes:asc", "title:desc"],
            "cursor": "opaque-cursor",
            "limit": 7,
        }
    )

    async with _catalog_client(handler) as client:
        result = await client.query_recipes(query, TOKEN)

    assert result.items[0].recipe.id == RECIPE_ID
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/recipes"
    assert request.url.params.multi_items() == [
        ("text", "tomato soup"),
        ("requiredIngredient", "basil"),
        ("requiredIngredient", "tomato"),
        ("availableIngredient", "salt"),
        ("availableIngredient", "water"),
        ("requiredTag", "dinner"),
        ("requiredTag", "quick"),
        ("preferredTag", "family"),
        ("preferredTag", "weeknight"),
        ("maxTotalMinutes", "45"),
        ("minRating", "4"),
        ("ratingState", "rated"),
        ("sort", "rating:desc"),
        ("sort", "totalMinutes:asc"),
        ("sort", "title:desc"),
        ("cursor", "opaque-cursor"),
        ("limit", "7"),
    ]
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert "Idempotency-Key" not in request.headers
    assert "subject-secret" not in str(request.url)


@pytest.mark.asyncio
async def test_get_recipe_forwards_bearer_and_uses_fixed_path() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_recipe_view_payload())

    async with _catalog_client(handler) as client:
        result = await client.get_recipe(RECIPE_ID, TOKEN)

    assert result.id == RECIPE_ID
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == f"/v1/recipes/{RECIPE_ID}"
    assert seen[0].url.query == b""
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert "Content-Type" not in seen[0].headers


@pytest.mark.asyncio
async def test_create_recipe_sends_exact_json_and_idempotency_key() -> None:
    seen: list[httpx.Request] = []
    payload = _recipe_create()
    key = "idem-secret-key"

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=_recipe_view_payload())

    async with _catalog_client(handler) as client:
        result = await client.create_recipe(payload, key, TOKEN)

    assert result.id == RECIPE_ID
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/recipes"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Idempotency-Key"] == key
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "title": "Tomato soup",
        "sourceUrl": "https://example.com/tomato-soup",
        "servings": 4,
        "prepMinutes": 10,
        "cookMinutes": 20,
        "totalMinutes": 30,
        "ingredients": [
            {"rawText": "2 tomatoes", "name": "tomato", "quantity": "2", "unit": "piece"}
        ],
        "instructions": ["Chop the tomatoes", "Simmer until soft"],
        "tags": ["soup"],
    }


@pytest.mark.asyncio
async def test_rate_recipe_sends_exact_json_and_fixed_path() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"value": 4})

    async with _catalog_client(handler) as client:
        result = await client.rate_recipe(RECIPE_ID, 4, TOKEN)

    assert result.value == 4
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "PUT"
    assert request.url.path == f"/v1/recipes/{RECIPE_ID}/rating"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == b'{"value":4}'
    assert "Idempotency-Key" not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_category", "expected_scope"),
    [
        (401, "authentication_required", None),
        (403, "insufficient_scope", "recipes:read"),
    ],
)
async def test_authentication_errors_are_typed_without_upstream_details(
    status: int, expected_category: str, expected_scope: str | None
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "detail": "token=secret-token subject=secret-subject",
                "request_id": "internal-request-id",
                "errorCategory": "unknown_internal_category",
                "secret": "secret-body-value",
            },
        )

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.query_recipes(RecipeQueryRequest(), TOKEN)

    error = captured.value
    assert error.category == expected_category
    assert error.retryable is False
    assert error.required_scope == expected_scope
    assert error.retry_after is None
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_not_found_maps_to_safe_recipe_category() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"detail": "recipe secret body", "errorCategory": "not-found-internal"},
        )

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.get_recipe(RECIPE_ID, TOKEN)

    error = captured.value
    assert error.category == "recipe_not_found"
    assert error.retryable is False
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "expected_category", "body"),
    [
        ("create", "idempotency_conflict", {"errorCategory": "idempotency_conflict"}),
        ("query", "stale_recipe_query_cursor", {"errorCategory": "stale_recipe_query_cursor"}),
    ],
)
async def test_allowlisted_conflicts_are_preserved_without_body_fields(
    method: str, expected_category: str, body: dict[str, object]
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                **body,
                "detail": "payload secret body",
                "request_id": "secret-request-id",
                "unknown": "unknown-secret",
            },
        )

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            if method == "create":
                await client.create_recipe(_recipe_create(), "idem-secret-key", TOKEN)
            else:
                await client.query_recipes(RecipeQueryRequest(), TOKEN)

    error = captured.value
    assert error.category == expected_category
    assert error.retryable is False
    assert all(secret not in str(error) for secret in SECRET_VALUES)
    assert "payload secret body" not in str(error)
    assert "unknown-secret" not in str(error)


@pytest.mark.asyncio
async def test_validation_error_is_safe_and_query_specific() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": "field secret contains token secret-token",
                "errorCategory": "validation-error",
                "errors": [{"field": "title", "message": "secret-body-value"}],
            },
        )

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.query_recipes(RecipeQueryRequest(), TOKEN)

    error = captured.value
    assert error.category == "invalid_query"
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
        return httpx.Response(429, headers={"Retry-After": header}, json={"secret": "body"})

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.get_recipe(RECIPE_ID, TOKEN)

    error = captured.value
    assert error.category == "catalog_rate_limited"
    assert error.retryable is True
    assert error.retry_after == expected_retry_after
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_server_error_is_retryable_and_makes_one_upstream_call() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="upstream secret body")

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.get_recipe(RECIPE_ID, TOKEN)

    assert captured.value.category == "temporary_catalog_failure"
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
                "detail": "token=secret-token body=secret-body-value",
            },
        )

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.get_recipe(RECIPE_ID, TOKEN)

    error = captured.value
    assert error.category == "temporary_catalog_failure"
    assert error.retryable is False
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_unknown_conflict_category_uses_the_safe_nonretryable_fallback() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "errorCategory": "internal-secret-conflict",
                "detail": "token=secret-token body=secret-body-value",
            },
        )

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.get_recipe(RECIPE_ID, TOKEN)

    error = captured.value
    assert error.category == "temporary_catalog_failure"
    assert error.retryable is False
    assert all(secret not in str(error) for secret in SECRET_VALUES)


class _DecodingFailureResponse(httpx.Response):
    def aiter_bytes(self, chunk_size: int | None = None):
        async def chunks():
            raise httpx.DecodingError("secret compressed response")
            yield b""

        return chunks()


@pytest.mark.asyncio
async def test_malformed_content_encoding_maps_safely_and_makes_one_request() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _DecodingFailureResponse(200, json=_recipe_view_payload(), request=request)

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.get_recipe(RECIPE_ID, TOKEN)

    assert captured.value.category == "temporary_catalog_failure"
    assert captured.value.retryable is True
    assert all(secret not in str(captured.value) for secret in SECRET_VALUES)
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 500])
async def test_oversized_success_or_problem_response_is_aborted_and_closed(status: int) -> None:
    responses: list[httpx.Response] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(
            status,
            content=b"x" * 65_537,
            headers={"Content-Type": "application/problem+json"},
            request=request,
        )
        responses.append(response)
        return response

    async with _catalog_client(handler, max_response_bytes=65_536) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.get_recipe(RECIPE_ID, TOKEN)

    assert captured.value.category == "temporary_catalog_failure"
    assert captured.value.retryable is True
    assert responses and responses[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception_type", [TimeoutError, httpx.ReadTimeout, httpx.ConnectError, httpx.PoolTimeout]
)
async def test_transport_failures_are_safe_retryable_temporary_errors(
    exception_type: type[Exception],
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise exception_type("token=secret-token url=https://secret.example")

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.get_recipe(RECIPE_ID, TOKEN)

    error = captured.value
    assert error.category == "temporary_catalog_failure"
    assert error.retryable is True
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"not": "a recipe"}),
    ],
)
async def test_malformed_success_responses_are_safe_temporary_errors(
    response: httpx.Response,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return response

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.get_recipe(RECIPE_ID, TOKEN)

    error = captured.value
    assert error.category == "temporary_catalog_failure"
    assert error.retryable is True
    assert all(secret not in str(error) for secret in SECRET_VALUES)


@pytest.mark.asyncio
async def test_readiness_validates_catalog_health_shape_and_returns_safe_status() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "service": "catalog",
                "dependencies": {"postgres": "ok", "redis_cache": "degraded"},
                "unknown": "secret-body-value",
            },
        )

    async with _catalog_client(handler) as client:
        result = await client.readiness()

    assert result == {"catalog": "ok"}
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/health/ready"
    assert seen[0].url.query == b""
    assert "Authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_malformed_readiness_is_a_safe_temporary_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "dependencies": "secret"})

    async with _catalog_client(handler) as client:
        with pytest.raises(CatalogClientError) as captured:
            await client.readiness()

    assert captured.value.category == "temporary_catalog_failure"
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
        base_url="https://catalog.example",
        timeout=timeout,
    ) as http:
        client = CatalogClient(http)
        async with asyncio.timeout(0.5):
            with pytest.raises(CatalogClientError) as captured:
                await client.readiness()

    assert captured.value.category == "temporary_catalog_failure"
    assert captured.value.retryable is True
