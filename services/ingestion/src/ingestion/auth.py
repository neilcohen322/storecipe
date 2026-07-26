import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ingestion.config import Settings


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]
    claims: dict[str, Any]


class InvalidAccessToken(Exception):
    pass


class Auth0TokenVerifier:
    """Validate Auth0 RS256 access tokens against issuer, audience, and JWKS."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        resolved_jwks_url = getattr(settings, "resolved_jwks_url", "")
        self._jwk_client = jwt.PyJWKClient(resolved_jwks_url) if resolved_jwks_url else None

    def _decode(self, token: str) -> dict[str, Any]:
        issuer = getattr(self._settings, "auth0_issuer", "")
        audience = getattr(self._settings, "auth0_audience", "")
        if self._jwk_client is None or not issuer or not audience:
            raise InvalidAccessToken("Auth0 is not configured")
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=audience,
                issuer=issuer,
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


bearer_scheme = HTTPBearer(auto_error=False)


def _challenge(*, required_scopes: tuple[str, ...] = (), error: str | None = None) -> str:
    parts: list[str] = []
    if error is not None:
        parts.append(f'error="{error}"')
    if required_scopes:
        parts.append(f'scope="{" ".join(required_scopes)}"')
    return "Bearer" if not parts else "Bearer " + ", ".join(parts)


def get_token_verifier(request: Request) -> Auth0TokenVerifier:
    verifier: Auth0TokenVerifier = request.app.state.token_verifier
    return verifier


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[Auth0TokenVerifier, Depends(get_token_verifier)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": _challenge()},
        )
    try:
        return await verifier.verify(credentials.credentials)
    except InvalidAccessToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token.",
            headers={"WWW-Authenticate": _challenge(error="invalid_token")},
        ) from exc


PrincipalDependency = Callable[..., Awaitable[Principal]]


def require_scopes(*required_scopes: str) -> PrincipalDependency:
    async def check_scopes(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        missing = set(required_scopes) - principal.scopes
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
                headers={
                    "WWW-Authenticate": _challenge(
                        required_scopes=required_scopes,
                        error="insufficient_scope",
                    )
                },
            )
        return principal

    return check_scopes
