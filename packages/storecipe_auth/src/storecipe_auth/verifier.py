import asyncio
from typing import Any, Protocol

import jwt

from storecipe_auth.principal import InvalidAccessToken, Principal


class Auth0JwtSettings(Protocol):
    """Minimal settings surface required to validate Auth0 access tokens."""

    @property
    def auth0_issuer(self) -> str: ...

    @property
    def resolved_jwks_url(self) -> str: ...


class Auth0TokenVerifier:
    """Validate Auth0 RS256 access tokens against issuer, audience, and JWKS."""

    def __init__(
        self,
        settings: Auth0JwtSettings,
        *,
        audience: str,
        jwk_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._audience = audience
        if jwk_client is not None:
            self._jwk_client = jwk_client
        else:
            resolved_jwks_url = settings.resolved_jwks_url
            self._jwk_client = (
                jwt.PyJWKClient(resolved_jwks_url) if resolved_jwks_url else None
            )

    def _decode(self, token: str) -> dict[str, Any]:
        if self._jwk_client is None or not self._settings.auth0_issuer or not self._audience:
            raise InvalidAccessToken("Auth0 is not configured")
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
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
