import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from catalog.auth import Auth0TokenVerifier, InvalidAccessToken
from catalog.config import Settings


class StaticJwkClient:
    def __init__(self, public_key: rsa.RSAPublicKey) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
        return SimpleNamespace(key=self._public_key)


def verifier_with_key(public_key: rsa.RSAPublicKey) -> Auth0TokenVerifier:
    settings = Settings(
        auth0_issuer="https://tenant.example/",
        auth0_audience="https://api.storecipe.example",
        auth0_jwks_url="https://tenant.example/.well-known/jwks.json",
    )
    verifier = Auth0TokenVerifier(
        settings,
        audience=settings.auth0_audience,
        jwk_client=StaticJwkClient(public_key),
    )
    return verifier


@pytest.mark.asyncio
async def test_auth0_verifier_checks_signature_issuer_audience_and_scopes() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = verifier_with_key(private_key.public_key())
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "auth0|user-123",
            "iss": "https://tenant.example/",
            "aud": "https://api.storecipe.example",
            "iat": now,
            "exp": now + 300,
            "scope": "recipes:read recipes:write",
        },
        private_key,
        algorithm="RS256",
    )

    principal = await verifier.verify(token)

    assert principal.subject == "auth0|user-123"
    assert principal.scopes == frozenset({"recipes:read", "recipes:write"})


@pytest.mark.asyncio
async def test_auth0_verifier_rejects_wrong_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = verifier_with_key(private_key.public_key())
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "auth0|user-123",
            "iss": "https://tenant.example/",
            "aud": "https://another-api.example",
            "iat": now,
            "exp": now + 300,
        },
        private_key,
        algorithm="RS256",
    )

    with pytest.raises(InvalidAccessToken):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_auth0_verifier_rejects_mcp_audience_tokens() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = verifier_with_key(private_key.public_key())
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "auth0|user-123",
            "iss": "https://tenant.example/",
            "aud": "https://mcp.storecipe.example/mcp",
            "iat": now,
            "exp": now + 300,
            "scope": "recipes:read",
        },
        private_key,
        algorithm="RS256",
    )

    with pytest.raises(InvalidAccessToken):
        await verifier.verify(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("issuer", "expires_at"),
    [
        ("https://wrong-tenant.example/", int(time.time()) + 300),
        ("https://tenant.example/", int(time.time()) - 1),
    ],
)
async def test_auth0_verifier_rejects_wrong_issuer_or_expired_token(
    issuer: str, expires_at: int
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = verifier_with_key(private_key.public_key())
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "auth0|user-123",
            "iss": issuer,
            "aud": "https://api.storecipe.example",
            "iat": now - 10,
            "exp": expires_at,
        },
        private_key,
        algorithm="RS256",
    )

    with pytest.raises(InvalidAccessToken):
        await verifier.verify(token)
