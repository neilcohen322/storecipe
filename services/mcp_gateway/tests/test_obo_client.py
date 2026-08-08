from __future__ import annotations

import asyncio
import contextlib
import gc
import json

import httpx
import pytest
from pydantic import SecretStr

from storecipe_mcp.config import Settings
from storecipe_mcp.errors import CatalogClientError
from storecipe_mcp.obo_client import OboTokenProvider, _cache_key, build_obo_token_provider

MCP_TOKEN = "inbound-mcp-token"
API_TOKEN = "exchanged-api-token"


def _assert_token_exchange_request(request: httpx.Request) -> str:
    assert request.method == "POST"
    assert request.url.path.endswith("/oauth/token")
    assert request.headers["content-type"].startswith("application/json")
    payload = json.loads(request.read().decode("utf-8"))
    assert payload["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert payload["client_id"] == "obo-client"
    assert payload["client_secret"] == "obo-secret"
    assert payload["subject_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
    assert payload["requested_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
    assert payload["audience"] == "https://api.storecipe.example"
    subject_token = payload["subject_token"]
    assert isinstance(subject_token, str) and subject_token
    assert set(payload) == {
        "grant_type",
        "client_id",
        "client_secret",
        "subject_token",
        "subject_token_type",
        "requested_token_type",
        "audience",
    }
    return subject_token


def _exchange_handler(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        _assert_token_exchange_request(request)
        return httpx.Response(
            200,
            json={"access_token": API_TOKEN, "expires_in": 300, "token_type": "Bearer"},
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_obo_provider_exchanges_subject_token_for_api_audience_token() -> None:
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_exchange_handler(requests)) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret=SecretStr("obo-secret"),
            api_audience="https://api.storecipe.example",
        )

        token = await provider.get_api_token(MCP_TOKEN)

    assert token == API_TOKEN
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_obo_provider_caches_exchanges_until_expiry_margin() -> None:
    requests: list[httpx.Request] = []
    clock = {"now": 100.0}

    async with httpx.AsyncClient(transport=_exchange_handler(requests)) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
            expiry_margin_seconds=30.0,
            clock=lambda: clock["now"],
        )

        first = await provider.get_api_token(MCP_TOKEN)
        second = await provider.get_api_token(MCP_TOKEN)
        clock["now"] = 380.0
        third = await provider.get_api_token(MCP_TOKEN)

    assert first == API_TOKEN
    assert second == API_TOKEN
    assert third == API_TOKEN
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_obo_provider_invalidate_forces_fresh_exchange() -> None:
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_exchange_handler(requests)) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
        )

        await provider.get_api_token(MCP_TOKEN)
        await provider.invalidate(MCP_TOKEN)
        await provider.get_api_token(MCP_TOKEN)

    assert len(requests) == 2


@pytest.mark.asyncio
async def test_obo_provider_maps_auth_failures_to_safe_categories() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
        )

        with pytest.raises(CatalogClientError) as exc_info:
            await provider.get_api_token(MCP_TOKEN)

    assert exc_info.value.category == "authentication_required"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_obo_provider_maps_upstream_outages_to_retryable_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
        )

        with pytest.raises(CatalogClientError) as exc_info:
            await provider.get_api_token(MCP_TOKEN)

    assert exc_info.value.category == "temporary_catalog_failure"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_obo_provider_maps_client_config_failures_to_terminal_catalog_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
        )

        with pytest.raises(CatalogClientError) as exc_info:
            await provider.get_api_token(MCP_TOKEN)

    assert exc_info.value.category == "temporary_catalog_failure"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "oauth_error",
    ["unauthorized_client", "invalid_audience", "unsupported_grant_type"],
)
async def test_obo_provider_maps_other_config_errors_to_terminal_catalog_errors(
    oauth_error: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": oauth_error}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
        )

        with pytest.raises(CatalogClientError) as exc_info:
            await provider.get_api_token(MCP_TOKEN)

    assert exc_info.value.category == "temporary_catalog_failure"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_obo_provider_prunes_expired_cache_entries_before_enforcing_limit() -> None:
    requests: list[httpx.Request] = []
    clock = {"now": 100.0}

    async with httpx.AsyncClient(transport=_exchange_handler(requests)) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
            expiry_margin_seconds=30.0,
            max_cache_entries=2,
            clock=lambda: clock["now"],
        )

        await provider.get_api_token("token-a")
        clock["now"] = 500.0
        await provider.get_api_token("token-b")
        await provider.get_api_token("token-c")

    assert len(provider._cache) == 2
    assert _cache_key("token-a") not in provider._cache
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_obo_provider_evicts_oldest_entry_when_cache_is_full() -> None:
    requests: list[httpx.Request] = []
    clock = {"now": 100.0}

    async with httpx.AsyncClient(transport=_exchange_handler(requests)) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
            expiry_margin_seconds=30.0,
            max_cache_entries=2,
            clock=lambda: clock["now"],
        )

        await provider.get_api_token("token-a")
        await provider.get_api_token("token-b")
        await provider.get_api_token("token-c")

        assert len(provider._cache) == 2
        assert _cache_key("token-a") not in provider._cache

        await provider.get_api_token("token-b")
        await provider.get_api_token("token-c")

    assert len(requests) == 3


@pytest.mark.asyncio
async def test_obo_provider_single_flights_duplicate_exchanges_for_one_token() -> None:
    requests: list[httpx.Request] = []
    release = asyncio.Event()
    entered = asyncio.Event()
    first: asyncio.Task[str] | None = None
    second: asyncio.Task[str] | None = None

    class GateTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requests.append(request)
            _assert_token_exchange_request(request)
            entered.set()
            await release.wait()
            return httpx.Response(
                200,
                json={"access_token": API_TOKEN, "expires_in": 300, "token_type": "Bearer"},
                request=request,
            )

    async with httpx.AsyncClient(transport=GateTransport()) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
        )
        try:
            first = asyncio.create_task(provider.get_api_token(MCP_TOKEN))
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            second = asyncio.create_task(provider.get_api_token(MCP_TOKEN))
            await asyncio.sleep(0)
            release.set()
            assert await asyncio.wait_for(first, timeout=1.0) == API_TOKEN
            assert await asyncio.wait_for(second, timeout=1.0) == API_TOKEN
        finally:
            release.set()
            for task in (first, second):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_obo_exchange() -> None:
    requests: list[httpx.Request] = []
    release = asyncio.Event()
    entered = asyncio.Event()
    first: asyncio.Task[str] | None = None
    second: asyncio.Task[str] | None = None

    class GateTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requests.append(request)
            _assert_token_exchange_request(request)
            entered.set()
            await release.wait()
            return httpx.Response(
                200,
                json={"access_token": API_TOKEN, "expires_in": 300, "token_type": "Bearer"},
                request=request,
            )

    async with httpx.AsyncClient(transport=GateTransport()) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
        )
        try:
            first = asyncio.create_task(provider.get_api_token(MCP_TOKEN))
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            second = asyncio.create_task(provider.get_api_token(MCP_TOKEN))
            await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            assert await asyncio.wait_for(second, timeout=1.0) == API_TOKEN
        finally:
            release.set()
            for task in (first, second):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_cancelled_waiters_consume_detached_exchange_failures() -> None:
    release = asyncio.Event()
    entered = asyncio.Event()
    waiter: asyncio.Task[str] | None = None
    contexts: list[dict[str, object]] = []

    class FailingGateTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            _assert_token_exchange_request(request)
            entered.set()
            await release.wait()
            return httpx.Response(503, request=request)

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def capture(_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        contexts.append(context)

    loop.set_exception_handler(capture)
    try:
        async with httpx.AsyncClient(transport=FailingGateTransport()) as http:
            provider = OboTokenProvider(
                http=http,
                token_url="https://tenant.example/oauth/token",
                client_id="obo-client",
                client_secret="obo-secret",
                api_audience="https://api.storecipe.example",
            )
            try:
                waiter = asyncio.create_task(provider.get_api_token(MCP_TOKEN))
                await asyncio.wait_for(entered.wait(), timeout=1.0)
                exchange = next(iter(provider._inflight.values()))
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
                release.set()
                done, pending = await asyncio.wait({exchange}, timeout=1.0)
                assert exchange in done and not pending
                assert not provider._inflight
                # Drop the last strong reference without retrieving the exception.
                del exchange
                await asyncio.sleep(0)
                gc.collect()
            finally:
                release.set()
                if waiter is not None and not waiter.done():
                    waiter.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await waiter
    finally:
        loop.set_exception_handler(previous_handler)

    assert not any("never retrieved" in str(context.get("message", "")) for context in contexts)


@pytest.mark.asyncio
async def test_obo_provider_does_not_globally_serialize_distinct_token_exchanges() -> None:
    release = asyncio.Event()
    active = 0
    max_active = 0
    started = 0
    lock = asyncio.Lock()
    first: asyncio.Task[str] | None = None
    second: asyncio.Task[str] | None = None

    class ConcurrentTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal active, max_active, started
            subject = _assert_token_exchange_request(request)
            async with lock:
                active += 1
                started += 1
                max_active = max(max_active, active)
            await release.wait()
            async with lock:
                active -= 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"api-for-{subject}",
                    "expires_in": 300,
                    "token_type": "Bearer",
                },
                request=request,
            )

    async with httpx.AsyncClient(transport=ConcurrentTransport()) as http:
        provider = OboTokenProvider(
            http=http,
            token_url="https://tenant.example/oauth/token",
            client_id="obo-client",
            client_secret="obo-secret",
            api_audience="https://api.storecipe.example",
        )
        try:
            first = asyncio.create_task(provider.get_api_token("token-a"))
            second = asyncio.create_task(provider.get_api_token("token-b"))
            for _ in range(50):
                async with lock:
                    if started >= 2:
                        break
                await asyncio.sleep(0)
            async with lock:
                assert started == 2
                assert max_active == 2
            release.set()
            assert await asyncio.wait_for(first, timeout=1.0) == "api-for-token-a"
            assert await asyncio.wait_for(second, timeout=1.0) == "api-for-token-b"
        finally:
            release.set()
            for task in (first, second):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task


def test_build_obo_token_provider_uses_gateway_settings() -> None:
    settings = Settings(
        auth0_issuer="https://tenant.example/",
        auth0_audience="https://api.storecipe.example",
        obo_client_id="obo-client",
        obo_client_secret=SecretStr("obo-secret"),
    )

    provider = build_obo_token_provider(settings, httpx.AsyncClient())

    assert provider._token_url == "https://tenant.example/oauth/token"
    assert provider._api_audience == "https://api.storecipe.example"
    assert provider._client_id == "obo-client"
