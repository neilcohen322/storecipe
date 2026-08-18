import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from storecipe_auth.principal import InvalidAccessToken
from storecipe_auth.verifier import (
    MAX_ACCESS_TOKEN_CHARS,
    MAX_KID_CHARS,
    Auth0TokenVerifier,
)


class _JwtSettings:
    auth0_issuer = "https://tenant.example/"
    resolved_jwks_url = "https://tenant.example/.well-known/jwks.json"


class StaticJwkClient:
    def __init__(self, public_key: rsa.RSAPublicKey) -> None:
        self.public_key = public_key
        self.fetch_calls = 0

    def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
        self.fetch_calls += 1
        return SimpleNamespace(key=self.public_key)


class RecordingJwksClient:
    def __init__(
        self,
        cached: dict[str, rsa.RSAPublicKey],
        refreshed: dict[str, rsa.RSAPublicKey] | None = None,
    ) -> None:
        self.cached = cached
        self.refreshed = refreshed if refreshed is not None else dict(cached)
        self.refresh_calls = 0
        self.cache_calls = 0

    def get_signing_keys(self, refresh: bool = False) -> list[SimpleNamespace]:
        if refresh:
            self.refresh_calls += 1
            self.cached = dict(self.refreshed)
            mapping = self.refreshed
        else:
            self.cache_calls += 1
            mapping = self.cached
        return [SimpleNamespace(key_id=kid, key=key) for kid, key in mapping.items()]


def _verifier(
    jwk_client: Any,
    *,
    cooldown: float = 5.0,
    clock: Any = None,
) -> Auth0TokenVerifier:
    return Auth0TokenVerifier(
        _JwtSettings(),
        audience="https://api.storecipe.example",
        jwk_client=jwk_client,
        jwks_miss_cooldown_seconds=cooldown,
        clock=clock,
    )


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = "test-key",
    alg: str = "RS256",
    extra_claims: dict[str, object] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": "auth0|user-123",
        "iss": "https://tenant.example/",
        "aud": "https://api.storecipe.example",
        "iat": now,
        "exp": now + 300,
        "scope": "recipes:read",
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, private_key, algorithm=alg, headers={"kid": kid, "alg": alg})


@pytest.mark.asyncio
async def test_oversized_malformed_wrong_algorithm_and_invalid_kid_cause_no_jwks_fetch() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = StaticJwkClient(private_key.public_key())
    verifier = _verifier(client)
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "sub": "auth0|user-123",
            "iss": "https://tenant.example/",
            "aud": "https://api.storecipe.example",
            "iat": now,
            "exp": now + 300,
        },
        "secret" * 6,
        algorithm="HS256",
        headers={"kid": "test-key"},
    )
    cases = [
        "",
        "a" * (MAX_ACCESS_TOKEN_CHARS + 1),
        "only-one-segment",
        "one.two.three.four",
        "@@@.@@@.@@@",
        unsigned,
        jwt.encode(
            {
                "sub": "auth0|user-123",
                "iss": "https://tenant.example/",
                "aud": "https://api.storecipe.example",
                "iat": now,
                "exp": now + 300,
            },
            private_key,
            algorithm="RS256",
        ),
        _token(private_key, kid=""),
        _token(private_key, kid="k" * (MAX_KID_CHARS + 1)),
    ]

    for token in cases:
        with pytest.raises(InvalidAccessToken, match="Access token validation failed"):
            await verifier.verify(token)

    assert client.fetch_calls == 0


@pytest.mark.asyncio
async def test_unknown_kids_are_coalesced_and_rotation_still_recovers() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    clock = {"now": 0.0}
    client = RecordingJwksClient(cached={}, refreshed={})
    verifier = _verifier(client, cooldown=5.0, clock=lambda: clock["now"])

    with pytest.raises(InvalidAccessToken):
        await verifier.verify(_token(private_key, kid="kid-1"))
    first_refreshes = client.refresh_calls
    assert first_refreshes == 1

    with pytest.raises(InvalidAccessToken):
        await verifier.verify(_token(private_key, kid="kid-2"))
    assert client.refresh_calls == first_refreshes

    client.refreshed = {"rotated": public_key}
    clock["now"] = 5.0
    principal = await verifier.verify(_token(private_key, kid="rotated"))

    assert principal.subject == "auth0|user-123"
    assert client.refresh_calls == first_refreshes + 1

    cached_hits_before = client.cache_calls
    await verifier.verify(_token(private_key, kid="rotated"))
    assert client.refresh_calls == first_refreshes + 1
    assert client.cache_calls > cached_hits_before
