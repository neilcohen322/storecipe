import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mcp.server.auth.middleware.auth_context import get_access_token as _get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier

from storecipe_mcp.config import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]
    claims: dict[str, Any]


class InvalidAccessToken(Exception):
    """Raised when an Auth0 access token cannot be trusted."""


class Auth0TokenVerifier:
    """Validate Auth0 RS256 access tokens against issuer, API audience, and JWKS."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        resolved_jwks_url = settings.resolved_jwks_url
        self._jwk_client = jwt.PyJWKClient(resolved_jwks_url) if resolved_jwks_url else None

    def _decode(self, token: str, *, audience: str) -> dict[str, Any]:
        if self._jwk_client is None or not self._settings.auth0_issuer or not audience:
            raise InvalidAccessToken("Auth0 is not configured")
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=audience,
                issuer=self._settings.auth0_issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken("Access token validation failed") from exc
        return dict(claims)

    async def verify(self, token: str) -> Principal:
        if not self._settings.auth0_audience:
            raise InvalidAccessToken("Auth0 is not configured")
        claims = await asyncio.to_thread(
            self._decode,
            token,
            audience=self._settings.auth0_audience,
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidAccessToken("Access token subject is missing")
        raw_scope = claims.get("scope", "")
        scopes = frozenset(raw_scope.split()) if isinstance(raw_scope, str) else frozenset()
        return Principal(subject=subject, scopes=scopes, claims=claims)


class McpInboundTokenVerifier:
    """Validate inbound MCP access tokens against the canonical MCP resource audience."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        resolved_jwks_url = settings.resolved_jwks_url
        self._jwk_client = jwt.PyJWKClient(resolved_jwks_url) if resolved_jwks_url else None

    def _decode(self, token: str) -> dict[str, Any]:
        if (
            self._jwk_client is None
            or not self._settings.auth0_issuer
            or not self._settings.mcp_resource_url
        ):
            raise InvalidAccessToken("Auth0 is not configured")
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.mcp_resource_url,
                issuer=self._settings.auth0_issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken("Access token validation failed") from exc
        return dict(claims)

    async def verify(self, token: str) -> Principal:
        claims = await asyncio.to_thread(self._decode, token)
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidAccessToken("Access token subject is missing")
        raw_scope = claims.get("scope", "")
        scopes = frozenset(raw_scope.split()) if isinstance(raw_scope, str) else frozenset()
        return Principal(subject=subject, scopes=scopes, claims=claims)


class McpAuth0TokenVerifier(TokenVerifier):
    """Adapt the MCP inbound Auth0 verifier to the MCP SDK verifier protocol."""

    def __init__(self, verifier: McpInboundTokenVerifier, resource_url: str) -> None:
        self._verifier = verifier
        self._resource_url = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            principal = await self._verifier.verify(token)
        except InvalidAccessToken:
            return None

        claims = principal.claims
        raw_expiry = claims.get("exp")
        expires_at = raw_expiry if isinstance(raw_expiry, int) else None
        raw_client_id = claims.get("azp") or claims.get("client_id")
        client_id = raw_client_id if isinstance(raw_client_id, str) else principal.subject
        return AccessToken(
            token=token,
            client_id=client_id,
            subject=principal.subject,
            scopes=sorted(principal.scopes),
            expires_at=expires_at,
            resource=self._resource_url,
            claims=claims,
        )


McpTokenVerifier = McpAuth0TokenVerifier


def get_access_token() -> AccessToken | None:
    return _get_access_token()


def oauth_challenge(
    settings: Settings,
    scope: str | None = None,
    *,
    required_scopes: tuple[str, ...] = (),
    error: str | None = None,
    error_description: str | None = None,
) -> str:
    if scope is not None and not required_scopes:
        required_scopes = (scope,)
    parts = [f'resource_metadata="{settings.resource_metadata_url}"']
    if error is not None:
        parts.append(f'error="{error}"')
    if error_description is not None:
        parts.append(f'error_description="{error_description}"')
    if required_scopes:
        parts.append(f'scope="{" ".join(required_scopes)}"')
    return "Bearer " + ", ".join(parts)


bearer_scheme = HTTPBearer(auto_error=False)


def _settings_for_request(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


def _challenge(
    *, settings: Settings, required_scopes: tuple[str, ...] = (), error: str | None = None
) -> str:
    return oauth_challenge(
        settings,
        required_scopes=required_scopes,
        error=error,
        error_description=(
            "The access token lacks a required scope." if error == "insufficient_scope" else None
        ),
    )


def get_token_verifier(request: Request) -> Auth0TokenVerifier:
    verifier: Auth0TokenVerifier = request.app.state.token_verifier
    return verifier


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[Auth0TokenVerifier, Depends(get_token_verifier)],
) -> Principal:
    settings = _settings_for_request(request)
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": _challenge(settings=settings)},
        )
    try:
        return await verifier.verify(credentials.credentials)
    except InvalidAccessToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token.",
            headers={"WWW-Authenticate": _challenge(settings=settings, error="invalid_token")},
        ) from exc


PrincipalDependency = Callable[..., Awaitable[Principal]]


def require_scopes(*required_scopes: str) -> PrincipalDependency:
    async def check_scopes(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        missing = set(required_scopes) - principal.scopes
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
                headers={
                    "WWW-Authenticate": _challenge(
                        settings=_settings_for_request(request),
                        required_scopes=required_scopes,
                        error="insufficient_scope",
                    )
                },
            )
        return principal

    return check_scopes
