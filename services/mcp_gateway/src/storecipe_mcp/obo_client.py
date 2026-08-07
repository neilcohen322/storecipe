"""Auth0 RFC 8693 On-Behalf-Of token exchange for Catalog REST calls."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from storecipe_mcp.config import Settings
from storecipe_mcp.errors import CatalogClientError

_TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
_ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
DEFAULT_EXPIRY_MARGIN_SECONDS = 30.0
DEFAULT_MAX_CACHE_ENTRIES = 256
_SUBJECT_TOKEN_ERRORS = frozenset({"invalid_grant", "invalid_token"})
_CONFIG_ERRORS = frozenset(
    {
        "invalid_client",
        "unauthorized_client",
        "invalid_audience",
        "unsupported_grant_type",
    }
)


@dataclass(frozen=True, slots=True)
class _CachedExchange:
    api_token: str
    expires_at: float


class OboTokenProvider:
    """Exchange inbound MCP tokens for API-audience tokens with bounded caching."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        token_url: str,
        client_id: str,
        client_secret: SecretStr | str,
        api_audience: str,
        expiry_margin_seconds: float = DEFAULT_EXPIRY_MARGIN_SECONDS,
        max_cache_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = (
            client_secret.get_secret_value()
            if isinstance(client_secret, SecretStr)
            else client_secret
        )
        self._api_audience = api_audience
        self._expiry_margin_seconds = expiry_margin_seconds
        self._max_cache_entries = max_cache_entries
        self._clock = clock
        self._cache: OrderedDict[str, _CachedExchange] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get_api_token(self, subject_token: str) -> str:
        """Return a cached API token for ``subject_token``, exchanging when needed."""
        cache_key = _cache_key(subject_token)
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None and self._has_usable_exchange(cached):
                self._cache.move_to_end(cache_key)
                return cached.api_token
            api_token, expires_in = await self._exchange(subject_token)
            self._store_exchange(
                cache_key,
                _CachedExchange(
                    api_token=api_token,
                    expires_at=self._clock() + expires_in,
                ),
            )
            return api_token

    async def invalidate(self, subject_token: str) -> None:
        """Discard a cached exchange for ``subject_token`` if it is still current."""
        cache_key = _cache_key(subject_token)
        async with self._lock:
            self._cache.pop(cache_key, None)

    def _has_usable_exchange(self, cached: _CachedExchange) -> bool:
        return self._clock() < cached.expires_at - self._expiry_margin_seconds

    def _prune_cache(self) -> None:
        expired_keys = [
            key
            for key, cached in self._cache.items()
            if not self._has_usable_exchange(cached)
        ]
        for key in expired_keys:
            self._cache.pop(key, None)

    def _store_exchange(self, cache_key: str, exchange: _CachedExchange) -> None:
        self._prune_cache()
        self._cache[cache_key] = exchange
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._max_cache_entries:
            self._cache.popitem(last=False)

    async def _exchange(self, subject_token: str) -> tuple[str, float]:
        payload = {
            "grant_type": _TOKEN_EXCHANGE_GRANT,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "subject_token": subject_token,
            "subject_token_type": _ACCESS_TOKEN_TYPE,
            "requested_token_type": _ACCESS_TOKEN_TYPE,
            "audience": self._api_audience,
        }
        try:
            response = await self._http.post(self._token_url, json=payload)
        except (TimeoutError, httpx.HTTPError) as exc:
            raise CatalogClientError("temporary_catalog_failure", retryable=True) from exc

        if not 200 <= response.status_code < 300:
            body = _optional_error_body(response)
            raise _classify_exchange_failure(response.status_code, body)

        body = response.json()
        if not isinstance(body, Mapping):
            raise CatalogClientError("temporary_catalog_failure", retryable=True)

        api_token = body.get("access_token")
        expires_in = body.get("expires_in")
        if (
            not isinstance(api_token, str)
            or not api_token
            or not isinstance(expires_in, int | float)
            or isinstance(expires_in, bool)
            or expires_in <= 0
        ):
            raise CatalogClientError("temporary_catalog_failure", retryable=True)
        return api_token, float(expires_in)


def build_obo_token_provider(settings: Settings, http: httpx.AsyncClient) -> OboTokenProvider:
    """Build the process-scoped OBO provider from gateway settings."""
    return OboTokenProvider(
        http=http,
        token_url=settings.resolved_obo_token_url,
        client_id=settings.obo_client_id,
        client_secret=settings.obo_client_secret,
        api_audience=settings.auth0_audience,
        expiry_margin_seconds=settings.obo_expiry_margin_seconds,
    )


def _cache_key(subject_token: str) -> str:
    return hashlib.sha256(subject_token.encode("utf-8")).hexdigest()


def _optional_error_body(response: httpx.Response) -> Mapping[str, object]:
    if not 400 <= response.status_code < 500:
        return {}
    try:
        body = response.json()
    except (TypeError, ValueError):
        return {}
    return body if isinstance(body, Mapping) else {}


def _classify_exchange_failure(
    status_code: int,
    body: Mapping[str, object],
) -> CatalogClientError:
    if status_code == 429 or 500 <= status_code < 600:
        return CatalogClientError("temporary_catalog_failure", retryable=True)

    if 400 <= status_code < 500:
        error = body.get("error")
        if isinstance(error, str) and error in _CONFIG_ERRORS:
            return CatalogClientError("temporary_catalog_failure", retryable=False)
        if isinstance(error, str) and error in _SUBJECT_TOKEN_ERRORS:
            return CatalogClientError("authentication_required", retryable=False)
        return CatalogClientError("temporary_catalog_failure", retryable=False)

    return CatalogClientError("temporary_catalog_failure", retryable=True)
