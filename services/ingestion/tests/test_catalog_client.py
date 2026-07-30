"""Behavioral coverage for the worker's M2M Catalog boundary."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from aiohttp import web

from ingestion.catalog_client import CatalogClient, CatalogError, CatalogTokenProvider
from ingestion.import_models import RecipeImportCandidate


@asynccontextmanager
async def app_server(
    app: web.Application,
) -> AsyncIterator[str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    socket = site._server.sockets[0]
    try:
        yield f"http://127.0.0.1:{socket.getsockname()[1]}"
    finally:
        await runner.cleanup()


def candidate() -> RecipeImportCandidate:
    return RecipeImportCandidate.model_validate(
        {
            "title": "Imported stew",
            "source_url": "https://example.test/stew",
            "servings": 4,
            "prep_minutes": 10,
            "cook_minutes": 20,
            "total_minutes": 30,
            "ingredients": [
                {"raw_text": "2 carrots", "name": "carrot", "quantity": "2", "unit": None}
            ],
            "instructions": ["Simmer."],
            "tags": ["Dinner"],
        }
    )


def token_provider(base_url: str, **kwargs: Any) -> CatalogTokenProvider:
    return CatalogTokenProvider(
        token_url=f"{base_url}/oauth/token",
        client_id="worker-client",
        client_secret="worker-secret",
        audience="https://catalog.example.test",
        **kwargs,
    )


def status_handler(status: int) -> Callable[[web.Request], Awaitable[web.Response]]:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=status)

    return handler


def oauth_error_handler(error: str) -> Callable[[web.Request], Awaitable[web.Response]]:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response({"error": error}, status=401)

    return handler


@pytest.mark.asyncio
async def test_token_provider_caches_a_token_until_the_expiry_margin() -> None:
    """Removing the cache or expiry-margin branch would issue needless M2M requests."""

    requests = 0

    async def issue_token(_: web.Request) -> web.Response:
        nonlocal requests
        requests += 1
        return web.json_response({"access_token": f"token-{requests}", "expires_in": 60})

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    now = 100.0

    async with app_server(app) as base_url:
        provider = token_provider(base_url, clock=lambda: now, expiry_margin_seconds=30)
        assert await provider.get_token() == "token-1"
        assert await provider.get_token() == "token-1"
        now = 131.0
        assert await provider.get_token() == "token-2"

    assert requests == 2


@pytest.mark.asyncio
async def test_token_provider_synchronizes_concurrent_refreshes() -> None:
    """Removing the refresh lock would let one expired cache trigger many token requests."""

    requests = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def issue_token(_: web.Request) -> web.Response:
        nonlocal requests
        requests += 1
        started.set()
        await release.wait()
        return web.json_response({"access_token": "shared-token", "expires_in": 60})

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)

    async with app_server(app) as base_url:
        provider = token_provider(base_url)
        readers = [asyncio.create_task(provider.get_token()) for _ in range(8)]
        await started.wait()
        release.set()
        assert await asyncio.gather(*readers) == ["shared-token"] * 8

    assert requests == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_token_provider_marks_rate_limits_and_temporary_failures_retryable(
    status: int,
) -> None:
    """Treating an Auth0 rate limit or 5xx as terminal would discard a recoverable import."""

    app = web.Application()
    app.router.add_post("/oauth/token", status_handler(status))

    async with app_server(app) as base_url:
        with pytest.raises(CatalogError, match="token_request_failed") as captured:
            await token_provider(base_url).get_token()

    assert captured.value.retryable is True
    assert captured.value.status == status


@pytest.mark.asyncio
async def test_token_provider_marks_timeouts_retryable() -> None:
    """Classifying an Auth0 timeout as terminal would abandon a valid client-credentials flow."""

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_token(_: web.Request) -> web.Response:
        started.set()
        await release.wait()
        return web.json_response({"access_token": "too-late", "expires_in": 60})

    app = web.Application()
    app.router.add_post("/oauth/token", slow_token)

    async with app_server(app) as base_url:
        with pytest.raises(CatalogError, match="token_request_failed") as captured:
            await token_provider(base_url, timeout_seconds=0.01).get_token()
        assert captured.value.retryable is True
        await started.wait()
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("oauth_error", ["invalid_client", "invalid_audience"])
async def test_token_provider_marks_invalid_client_or_audience_terminal(oauth_error: str) -> None:
    """Retrying rejected client credentials or an invalid audience would only waste worker slots."""

    app = web.Application()
    app.router.add_post("/oauth/token", oauth_error_handler(oauth_error))

    async with app_server(app) as base_url:
        with pytest.raises(CatalogError, match="token_request_failed") as captured:
            await token_provider(base_url).get_token()

    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_catalog_client_uses_catalog_aliases_and_job_id_as_the_idempotency_key() -> None:
    """Changing the endpoint payload loses ownership or makes a replay create another recipe."""

    seen: dict[str, object] = {}
    recipe_id = uuid4()

    async def issue_token(_: web.Request) -> web.Response:
        return web.json_response({"access_token": "catalog-token", "expires_in": 60})

    async def create_recipe(request: web.Request) -> web.Response:
        seen["authorization"] = request.headers.get("Authorization")
        seen["payload"] = await request.json()
        return web.json_response({"id": str(recipe_id)}, status=201)

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/imported", create_recipe)

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        job_id = uuid4()
        assert (
            await client.create_imported(job_id, "auth0|owner", "a" * 64, candidate()) == recipe_id
        )

    assert seen == {
        "authorization": "Bearer catalog-token",
        "payload": {
            "title": "Imported stew",
            "sourceUrl": "https://example.test/stew",
            "servings": 4,
            "prepMinutes": 10,
            "cookMinutes": 20,
            "totalMinutes": 30,
            "ingredients": [
                {"rawText": "2 carrots", "name": "carrot", "quantity": 2, "unit": None}
            ],
            "instructions": ["Simmer."],
            "tags": ["Dinner"],
            "ownerSubject": "auth0|owner",
            "sourceFingerprint": "a" * 64,
            "importJobId": str(job_id),
        },
    }


@pytest.mark.asyncio
async def test_catalog_client_finds_existing_source() -> None:
    """Omitting the lookup request or its fingerprint would miss an existing recipe."""

    recipe_id = uuid4()

    async def issue_token(_: web.Request) -> web.Response:
        return web.json_response({"access_token": "catalog-token", "expires_in": 60})

    async def lookup(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer catalog-token"
        assert await request.json() == {
            "ownerSubject": "auth0|owner",
            "sourceFingerprint": "a" * 64,
        }
        return web.json_response({"recipeId": str(recipe_id)})

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/source-lookup", lookup)

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        assert await client.find_existing_source("auth0|owner", "a" * 64) == recipe_id


@pytest.mark.asyncio
async def test_catalog_client_returns_none_for_a_missing_source() -> None:
    """Treating an empty lookup result as an error would block a new recipe import."""

    async def issue_token(_: web.Request) -> web.Response:
        return web.json_response({"access_token": "catalog-token", "expires_in": 60})

    async def lookup(_: web.Request) -> web.Response:
        return web.json_response({"recipeId": None})

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/source-lookup", lookup)

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        assert await client.find_existing_source("auth0|owner", "a" * 64) is None


@pytest.mark.asyncio
async def test_catalog_client_refreshes_once_for_a_source_lookup() -> None:
    """Skipping a lookup refresh fails expired tokens; repeating it risks an auth loop."""

    issued = 0
    authorizations: list[str | None] = []
    recipe_id = uuid4()

    async def issue_token(_: web.Request) -> web.Response:
        nonlocal issued
        issued += 1
        return web.json_response({"access_token": f"token-{issued}", "expires_in": 60})

    async def lookup(request: web.Request) -> web.Response:
        authorizations.append(request.headers.get("Authorization"))
        if len(authorizations) == 1:
            return web.Response(status=401)
        return web.json_response({"recipeId": str(recipe_id)})

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/source-lookup", lookup)

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        assert await client.find_existing_source("auth0|owner", "a" * 64) == recipe_id

    assert issued == 2
    assert authorizations == ["Bearer token-1", "Bearer token-2"]


@pytest.mark.asyncio
async def test_catalog_client_retries_a_malformed_source_lookup_response() -> None:
    """Treating an unreadable lookup response as terminal loses a recoverable import."""

    async def issue_token(_: web.Request) -> web.Response:
        return web.json_response({"access_token": "catalog-token", "expires_in": 60})

    async def lookup(_: web.Request) -> web.Response:
        return web.Response(status=200, text="not-json", content_type="text/plain")

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/source-lookup", lookup)

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        with pytest.raises(CatalogError, match="catalog_response_invalid") as captured:
            await client.find_existing_source("auth0|owner", "a" * 64)

    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_catalog_client_rejects_a_source_lookup_response_without_recipe_id() -> None:
    """Treating a missing result field as a miss would bypass duplicate protection."""

    async def issue_token(_: web.Request) -> web.Response:
        return web.json_response({"access_token": "catalog-token", "expires_in": 60})

    async def lookup(_: web.Request) -> web.Response:
        return web.json_response({"unexpected": "value"})

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/source-lookup", lookup)

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        with pytest.raises(CatalogError, match="catalog_response_invalid") as captured:
            await client.find_existing_source("auth0|owner", "a" * 64)

    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_catalog_client_marks_source_lookup_503_retryable() -> None:
    """Making an unavailable source lookup terminal would abandon a recoverable import."""

    async def issue_token(_: web.Request) -> web.Response:
        return web.json_response({"access_token": "catalog-token", "expires_in": 60})

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/source-lookup", status_handler(503))

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        with pytest.raises(CatalogError, match="catalog_request_failed") as captured:
            await client.find_existing_source("auth0|owner", "a" * 64)

    assert captured.value.retryable is True
    assert captured.value.status == 503


@pytest.mark.asyncio
async def test_catalog_client_refreshes_once_after_an_unauthorized_response() -> None:
    """Skipping refresh fails expired tokens; repeating it risks an auth loop."""

    issued = 0
    authorizations: list[str | None] = []
    recipe_id = uuid4()

    async def issue_token(_: web.Request) -> web.Response:
        nonlocal issued
        issued += 1
        return web.json_response({"access_token": f"token-{issued}", "expires_in": 60})

    async def create_recipe(request: web.Request) -> web.Response:
        authorizations.append(request.headers.get("Authorization"))
        if len(authorizations) == 1:
            return web.Response(status=401)
        return web.json_response({"id": str(recipe_id)}, status=201)

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/imported", create_recipe)

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        assert (
            await client.create_imported(uuid4(), "auth0|owner", "a" * 64, candidate()) == recipe_id
        )

    assert issued == 2
    assert authorizations == ["Bearer token-1", "Bearer token-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_catalog_client_treats_second_unauthorized_and_forbidden_as_terminal(
    status: int,
) -> None:
    """Retrying an authorization rejection would hide a misconfiguration."""

    issued = 0
    requests = 0

    async def issue_token(_: web.Request) -> web.Response:
        nonlocal issued
        issued += 1
        return web.json_response({"access_token": f"token-{issued}", "expires_in": 60})

    async def create_recipe(_: web.Request) -> web.Response:
        nonlocal requests
        requests += 1
        return web.Response(status=status)

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/imported", create_recipe)

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        with pytest.raises(CatalogError, match="catalog_request_failed") as captured:
            await client.create_imported(uuid4(), "auth0|owner", "a" * 64, candidate())

    assert captured.value.retryable is False
    assert requests == (2 if status == 401 else 1)
    assert issued == (2 if status == 401 else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_catalog_client_marks_ambiguous_catalog_outcomes_retryable(status: int) -> None:
    """Making a rate limit or temporary Catalog failure terminal abandons an idempotent import."""

    async def issue_token(_: web.Request) -> web.Response:
        return web.json_response({"access_token": "catalog-token", "expires_in": 60})

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/imported", status_handler(status))

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        with pytest.raises(CatalogError, match="catalog_request_failed") as captured:
            await client.create_imported(uuid4(), "auth0|owner", "a" * 64, candidate())

    assert captured.value.retryable is True
    assert captured.value.status == status


@pytest.mark.asyncio
async def test_catalog_client_marks_an_unanswered_post_retryable() -> None:
    """Treating an unanswered POST as terminal loses a result Catalog may have committed."""

    started = asyncio.Event()
    release = asyncio.Event()

    async def issue_token(_: web.Request) -> web.Response:
        return web.json_response({"access_token": "catalog-token", "expires_in": 60})

    async def slow_create(_: web.Request) -> web.Response:
        started.set()
        await release.wait()
        return web.Response(status=201)

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/imported", slow_create)

    async with app_server(app) as base_url:
        client = CatalogClient(
            base_url=base_url,
            token_provider=token_provider(base_url),
            timeout_seconds=0.01,
        )
        with pytest.raises(CatalogError, match="catalog_request_failed") as captured:
            await client.create_imported(uuid4(), "auth0|owner", "a" * 64, candidate())
        assert captured.value.retryable is True
        await started.wait()
        release.set()


@pytest.mark.asyncio
async def test_catalog_client_marks_a_malformed_success_response_retryable() -> None:
    """An unreadable committed response can be recovered through its import-job key."""

    async def issue_token(_: web.Request) -> web.Response:
        return web.json_response({"access_token": "catalog-token", "expires_in": 60})

    async def malformed_success(_: web.Request) -> web.Response:
        return web.Response(status=201, text="not-json", content_type="text/plain")

    app = web.Application()
    app.router.add_post("/oauth/token", issue_token)
    app.router.add_post("/internal/recipes/imported", malformed_success)

    async with app_server(app) as base_url:
        client = CatalogClient(base_url=base_url, token_provider=token_provider(base_url))
        with pytest.raises(CatalogError, match="catalog_response_invalid") as captured:
            await client.create_imported(uuid4(), "auth0|owner", "a" * 64, candidate())

    assert captured.value.retryable is True
