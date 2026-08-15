"""M2M-authenticated boundary for Catalog's idempotent imported-recipe endpoint.

This module deliberately keeps failures small and typed.  Callers can persist only
the safe code/status/retryability fields; no token, recipe payload, URL, or response
body is included in an exception message.
"""

import asyncio
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any
from uuid import UUID

import aiohttp
from pydantic import SecretStr

from ingestion.config import Settings
from ingestion.import_models import RecipeImportCandidate

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_EXPIRY_MARGIN_SECONDS = 30.0


class CatalogFailureCode(StrEnum):
    """Safe categories for the Catalog and Auth0 boundary."""

    TOKEN_REQUEST_FAILED = "token_request_failed"
    TOKEN_RESPONSE_INVALID = "token_response_invalid"
    CATALOG_REQUEST_FAILED = "catalog_request_failed"
    CATALOG_RESPONSE_INVALID = "catalog_response_invalid"


class CatalogError(Exception):
    """A safe, typed boundary error suitable for durable retry classification."""

    def __init__(
        self,
        code: CatalogFailureCode,
        *,
        retryable: bool,
        status: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.retryable = retryable
        self.status = status


class CatalogTokenProvider:
    """Process-scoped synchronized client-credentials token cache."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: SecretStr | str,
        audience: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        expiry_margin_seconds: float = DEFAULT_EXPIRY_MARGIN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = (
            client_secret.get_secret_value()
            if isinstance(client_secret, SecretStr)
            else client_secret
        )
        self._audience = audience
        self._timeout_seconds = timeout_seconds
        self._expiry_margin_seconds = expiry_margin_seconds
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """Return a usable cached token, fetching at most once per concurrent refresh."""
        async with self._lock:
            token = self._token
            if self._has_usable_token() and token is not None:
                return token
            token, expires_in = await self._request_token()
            self._token = token
            self._expires_at = self._clock() + expires_in
            return token

    async def invalidate(self, token: str) -> None:
        """Discard ``token`` only if it is still current.

        The equality guard lets concurrent 401 handlers share the replacement
        obtained by the first handler instead of causing a refresh storm.
        """
        async with self._lock:
            if self._token == token:
                self._token = None
                self._expires_at = 0.0

    def _has_usable_token(self) -> bool:
        return (
            self._token is not None
            and self._clock() < self._expires_at - self._expiry_margin_seconds
        )

    async def _request_token(self) -> tuple[str, float]:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        payload = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "audience": self._audience,
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(self._token_url, json=payload) as response,
            ):
                if not 200 <= response.status < 300:
                    body = (
                        await _optional_response_object(response)
                        if 400 <= response.status < 500
                        else {}
                    )
                    raise CatalogError(
                        CatalogFailureCode.TOKEN_REQUEST_FAILED,
                        retryable=_token_failure_is_retryable(response.status, body),
                        status=response.status,
                    )
                body = await _required_response_object(
                    response, CatalogFailureCode.TOKEN_RESPONSE_INVALID
                )
        except CatalogError:
            raise
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise CatalogError(
                CatalogFailureCode.TOKEN_REQUEST_FAILED,
                retryable=True,
            ) from exc

        token = body.get("access_token")
        expires_in = body.get("expires_in")
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(expires_in, int | float)
            or isinstance(expires_in, bool)
            or expires_in <= 0
        ):
            raise CatalogError(CatalogFailureCode.TOKEN_RESPONSE_INVALID, retryable=False)
        return token, float(expires_in)


class CatalogClient:
    """Creates one Catalog recipe per durable ingestion job."""

    def __init__(
        self,
        *,
        base_url: str,
        token_provider: CatalogTokenProvider,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._create_endpoint = f"{base_url.rstrip('/')}/internal/recipes/imported"
        self._source_lookup_endpoint = f"{base_url.rstrip('/')}/internal/recipes/source-lookup"
        self._token_provider = token_provider
        self._timeout_seconds = timeout_seconds

    async def create_imported(
        self,
        job_id: UUID,
        owner_subject: str,
        source_fingerprint: str,
        candidate: RecipeImportCandidate,
    ) -> UUID:
        """Call Catalog once, with one forced refresh if its token is rejected."""
        payload = _catalog_payload(candidate)
        payload["ownerSubject"] = owner_subject
        payload["sourceFingerprint"] = source_fingerprint
        payload["importJobId"] = str(job_id)

        status, body = await self._post_with_token_refresh(
            self._create_endpoint, payload, {200, 201}
        )

        if status in {200, 201}:
            recipe_id = _response_uuid(body, "id")
            if recipe_id is None:
                raise CatalogError(CatalogFailureCode.CATALOG_RESPONSE_INVALID, retryable=True)
            return recipe_id
        raise CatalogError(
            CatalogFailureCode.CATALOG_REQUEST_FAILED,
            retryable=_catalog_failure_is_retryable(status),
            status=status,
        )

    async def find_existing_source(
        self, owner_subject: str, source_fingerprint: str
    ) -> UUID | None:
        """Return the recipe already imported from this source, if Catalog has one."""
        payload = {
            "ownerSubject": owner_subject,
            "sourceFingerprint": source_fingerprint,
        }
        status, body = await self._post_with_token_refresh(
            self._source_lookup_endpoint, payload, {200}
        )

        if status == 200:
            if body is None or "recipeId" not in body:
                raise CatalogError(CatalogFailureCode.CATALOG_RESPONSE_INVALID, retryable=True)
            recipe_id = _response_uuid(body, "recipeId")
            if recipe_id is not None or body["recipeId"] is None:
                return recipe_id
            raise CatalogError(CatalogFailureCode.CATALOG_RESPONSE_INVALID, retryable=True)
        raise CatalogError(
            CatalogFailureCode.CATALOG_REQUEST_FAILED,
            retryable=_catalog_failure_is_retryable(status),
            status=status,
        )

    async def _post_with_token_refresh(
        self,
        endpoint: str,
        payload: Mapping[str, Any],
        success_statuses: set[int],
    ) -> tuple[int, Mapping[str, Any] | None]:
        token = await self._token_provider.get_token()
        status, body = await self._post_authenticated(endpoint, token, payload, success_statuses)
        if status != 401:
            return status, body
        await self._token_provider.invalidate(token)
        refreshed_token = await self._token_provider.get_token()
        return await self._post_authenticated(endpoint, refreshed_token, payload, success_statuses)

    async def _post_authenticated(
        self,
        endpoint: str,
        token: str,
        payload: Mapping[str, Any],
        success_statuses: set[int],
    ) -> tuple[int, Mapping[str, Any] | None]:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                ) as response,
            ):
                if response.status not in success_statuses:
                    return response.status, None
                body = await _required_response_object(
                    response,
                    CatalogFailureCode.CATALOG_RESPONSE_INVALID,
                    retryable=True,
                )
        except CatalogError:
            raise
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise CatalogError(
                CatalogFailureCode.CATALOG_REQUEST_FAILED,
                retryable=True,
            ) from exc

        return response.status, body


def build_catalog_client(settings: Settings) -> CatalogClient:
    """Build the process-scoped Catalog boundary shared by API and worker."""

    token_provider = CatalogTokenProvider(
        token_url=settings.resolved_catalog_m2m_token_url,
        client_id=settings.catalog_m2m_client_id,
        client_secret=settings.catalog_m2m_client_secret,
        audience=settings.catalog_m2m_audience,
    )
    return CatalogClient(
        base_url=settings.catalog_api_url,
        token_provider=token_provider,
    )


async def _optional_response_object(response: aiohttp.ClientResponse) -> Mapping[str, Any]:
    try:
        body = await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


async def _required_response_object(
    response: aiohttp.ClientResponse,
    failure_code: CatalogFailureCode,
    *,
    retryable: bool = False,
) -> Mapping[str, Any]:
    body = await _optional_response_object(response)
    if not body:
        raise CatalogError(failure_code, retryable=retryable)
    return body


def _token_failure_is_retryable(status: int, body: Mapping[str, Any]) -> bool:
    error = body.get("error")
    if error in {"invalid_client", "invalid_audience"}:
        return False
    return status == 429 or 500 <= status <= 599


def _catalog_failure_is_retryable(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


def _response_uuid(body: Mapping[str, Any] | None, field: str) -> UUID | None:
    if body is None:
        return None
    raw_id = body.get(field)
    if raw_id is None:
        return None
    try:
        return UUID(raw_id) if isinstance(raw_id, str) else None
    except ValueError:
        return None


def _catalog_payload(candidate: RecipeImportCandidate) -> dict[str, Any]:
    """Serialize the ingestion model with Catalog's public request aliases.

    The two services intentionally do not share Pydantic models, so this
    explicit boundary mapping protects the HTTP contract from internal field
    naming changes.
    """
    return {
        "title": candidate.title,
        "sourceUrl": str(candidate.source_url) if candidate.source_url is not None else None,
        "servings": candidate.servings,
        "prepMinutes": candidate.prep_minutes,
        "cookMinutes": candidate.cook_minutes,
        "totalMinutes": candidate.total_minutes,
        "ingredients": [
            {
                "rawText": ingredient.raw_text,
                "name": ingredient.name,
                "canonicalName": ingredient.canonical_name,
                "quantity": float(ingredient.quantity) if ingredient.quantity is not None else None,
                "unit": ingredient.unit,
            }
            for ingredient in candidate.ingredients
        ],
        "instructions": candidate.instructions,
        "tags": candidate.tags,
    }
