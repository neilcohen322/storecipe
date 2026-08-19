import asyncio
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import jwt

from storecipe_auth.principal import InvalidAccessToken, Principal

MAX_ACCESS_TOKEN_CHARS = 4096
MAX_KID_CHARS = 128
DEFAULT_JWKS_MISS_COOLDOWN_SECONDS = 5.0
_GENERIC_TOKEN_FAILURE = "Access token validation failed"


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
        jwks_miss_cooldown_seconds: float = DEFAULT_JWKS_MISS_COOLDOWN_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._audience = audience
        self._jwks_miss_cooldown_seconds = jwks_miss_cooldown_seconds
        self._clock = clock or time.monotonic
        self._refresh_lock = threading.Lock()
        self._cooldown_until = 0.0
        if jwk_client is not None:
            self._jwk_client = jwk_client
        else:
            resolved_jwks_url = settings.resolved_jwks_url
            self._jwk_client = jwt.PyJWKClient(resolved_jwks_url) if resolved_jwks_url else None

    def _decode(self, token: str) -> dict[str, Any]:
        if self._jwk_client is None or not self._settings.auth0_issuer or not self._audience:
            raise InvalidAccessToken("Auth0 is not configured")
        header = _require_bounded_jwt_header(token)
        try:
            signing_key = self._resolve_signing_key(token, header["kid"])
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._settings.auth0_issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken(_GENERIC_TOKEN_FAILURE) from exc
        return dict(claims)

    def _resolve_signing_key(self, token: str, kid: str) -> Any:
        get_signing_keys = getattr(self._jwk_client, "get_signing_keys", None)
        if not callable(get_signing_keys):
            return self._jwk_client.get_signing_key_from_jwt(token)
        cached = _signing_key_for_kid(_signing_keys(get_signing_keys, refresh=False), kid)
        if cached is not None:
            return cached
        with self._refresh_lock:
            cached = _signing_key_for_kid(_signing_keys(get_signing_keys, refresh=False), kid)
            if cached is not None:
                return cached
            now = self._clock()
            if now < self._cooldown_until:
                raise InvalidAccessToken(_GENERIC_TOKEN_FAILURE)
            refreshed = _signing_key_for_kid(_signing_keys(get_signing_keys, refresh=True), kid)
            if refreshed is not None:
                self._cooldown_until = 0.0
                return refreshed
            self._cooldown_until = now + self._jwks_miss_cooldown_seconds
            raise InvalidAccessToken(_GENERIC_TOKEN_FAILURE)

    async def verify(self, token: str) -> Principal:
        claims = await asyncio.to_thread(self._decode, token)
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidAccessToken("Access token subject is missing")
        raw_scope = claims.get("scope", "")
        scopes = frozenset(raw_scope.split()) if isinstance(raw_scope, str) else frozenset()
        return Principal(subject=subject, scopes=scopes, claims=claims)


def _require_bounded_jwt_header(token: str) -> dict[str, Any]:
    if not token or not token.isascii() or len(token) > MAX_ACCESS_TOKEN_CHARS:
        raise InvalidAccessToken(_GENERIC_TOKEN_FAILURE)
    if token.count(".") != 2:
        raise InvalidAccessToken(_GENERIC_TOKEN_FAILURE)
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidAccessToken(_GENERIC_TOKEN_FAILURE) from exc
    if header.get("alg") != "RS256":
        raise InvalidAccessToken(_GENERIC_TOKEN_FAILURE)
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid or len(kid) > MAX_KID_CHARS:
        raise InvalidAccessToken(_GENERIC_TOKEN_FAILURE)
    return dict(header)


def _signing_keys(
    get_signing_keys: Callable[..., Sequence[Any]], *, refresh: bool
) -> Sequence[Any]:
    try:
        return get_signing_keys(refresh=refresh)
    except jwt.PyJWTError:
        return ()


def _signing_key_for_kid(keys: Sequence[Any], kid: str) -> Any | None:
    for key in keys:
        if getattr(key, "key_id", None) == kid:
            return key
    return None
