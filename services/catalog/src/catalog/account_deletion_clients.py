"""Safe two-audience M2M boundaries for the account-deletion saga."""

import asyncio
import time
from collections.abc import Callable
from urllib.parse import quote

import httpx
from pydantic import SecretStr


class DeletionBoundaryError(Exception):
    """A safe retry signal that never includes upstream bodies or credentials."""


class ClientCredentialsTokenProvider:
    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: SecretStr,
        audience: str,
        timeout_seconds: float = 10,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret.get_secret_value()
        self._audience = audience
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._token is not None and self._clock() < self._expires_at - 30:
                return self._token
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.post(
                        self._token_url,
                        json={
                            "grant_type": "client_credentials",
                            "client_id": self._client_id,
                            "client_secret": self._client_secret,
                            "audience": self._audience,
                        },
                    )
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise DeletionBoundaryError("token_request_failed") from exc
            token = body.get("access_token") if isinstance(body, dict) else None
            expires_in = body.get("expires_in") if isinstance(body, dict) else None
            if (
                not isinstance(token, str)
                or not token
                or not isinstance(expires_in, int | float)
                or isinstance(expires_in, bool)
                or expires_in <= 0
            ):
                raise DeletionBoundaryError("token_response_invalid")
            self._token = token
            self._expires_at = self._clock() + float(expires_in)
            return token


class AccountDeletionClients:
    def __init__(
        self,
        *,
        ingestion_base_url: str,
        auth0_management_base_url: str,
        internal_tokens: ClientCredentialsTokenProvider,
        auth0_tokens: ClientCredentialsTokenProvider,
        timeout_seconds: float = 10,
    ) -> None:
        self._ingestion_url = f"{ingestion_base_url.rstrip('/')}/internal/account-deletions"
        self._auth0_base = auth0_management_base_url.rstrip("/")
        self._internal_tokens = internal_tokens
        self._auth0_tokens = auth0_tokens
        self._timeout_seconds = timeout_seconds

    async def wipe_ingestion(self, subject: str, request_id: str) -> None:
        token = await self._internal_tokens.get_token()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    self._ingestion_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Request-ID": request_id,
                    },
                    json={"subject": subject},
                )
        except httpx.HTTPError as exc:
            raise DeletionBoundaryError("ingestion_delete_failed") from exc
        if response.status_code != 204:
            raise DeletionBoundaryError("ingestion_delete_failed")

    async def delete_auth0_user(self, subject: str, request_id: str) -> None:
        token = await self._auth0_tokens.get_token()
        endpoint = f"{self._auth0_base}/users/{quote(subject, safe='')}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.delete(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Request-ID": request_id,
                    },
                )
        except httpx.HTTPError as exc:
            raise DeletionBoundaryError("auth0_delete_failed") from exc
        if response.status_code not in {204, 404}:
            raise DeletionBoundaryError("auth0_delete_failed")
