from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from storecipe_mcp.config import Settings
from storecipe_mcp.errors import CatalogClientError
from storecipe_mcp.obo_client import OboTokenProvider, _cache_key, build_obo_token_provider

MCP_TOKEN = "inbound-mcp-token"
API_TOKEN = "exchanged-api-token"


def _exchange_handler(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path.endswith("/oauth/token")
        body = request.read().decode("utf-8")
        assert "urn:ietf:params:oauth:grant-type:token-exchange" in body
        assert "https://api.storecipe.example" in body
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
