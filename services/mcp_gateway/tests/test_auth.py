import time
from types import SimpleNamespace
from typing import Any, cast

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import AccessToken

from storecipe_mcp.auth import (
    Auth0TokenVerifier,
    InvalidAccessToken,
    McpAuth0TokenVerifier,
    oauth_challenge,
)
from storecipe_mcp.config import Settings


class StaticJwkClient:
    def __init__(self, public_key: rsa.RSAPublicKey) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
        return SimpleNamespace(key=self._public_key)


def _verifier(public_key: rsa.RSAPublicKey) -> Auth0TokenVerifier:
    settings = Settings(
        auth0_issuer="https://tenant.example/",
        auth0_audience="https://api.storecipe.example",
        auth0_jwks_url="https://tenant.example/.well-known/jwks.json",
    )
    verifier = Auth0TokenVerifier(settings)
    cast(Any, verifier)._jwk_client = StaticJwkClient(public_key)
    return verifier


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    issuer: str = "https://tenant.example/",
    audience: str | list[str] = "https://api.storecipe.example",
    expires_at: int | None = None,
    subject: str | None = "auth0|user-123",
    scope: str = "recipes:read recipes:write",
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": expires_at if expires_at is not None else now + 300,
        "scope": scope,
    }
    if subject is not None:
        claims["sub"] = subject
    return jwt.encode(claims, private_key, algorithm="RS256")


@pytest.mark.asyncio
async def test_auth0_verifier_checks_signature_issuer_audience_expiry_and_scope() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = _verifier(private_key.public_key())

    principal = await verifier.verify(_token(private_key))

    assert principal.subject == "auth0|user-123"
    assert principal.scopes == frozenset({"recipes:read", "recipes:write"})
    assert principal.claims["aud"] == "https://api.storecipe.example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"audience": "https://another-api.example"},
        {"issuer": "https://wrong-tenant.example/"},
        {"expires_at": int(time.time()) - 1},
        {"subject": None},
    ],
)
async def test_auth0_verifier_rejects_invalid_token_claims(kwargs: dict[str, object]) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = _verifier(private_key.public_key())

    with pytest.raises(InvalidAccessToken):
        await verifier.verify(_token(private_key, **kwargs))


@pytest.mark.asyncio
async def test_auth0_verifier_rejects_unconfigured_or_malformed_tokens() -> None:
    unconfigured = Auth0TokenVerifier(Settings())
    with pytest.raises(InvalidAccessToken, match="not configured"):
        await unconfigured.verify("not-a-jwt")

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = _verifier(private_key.public_key())
    with pytest.raises(InvalidAccessToken):
        await verifier.verify("not-a-jwt")


@pytest.mark.asyncio
async def test_mcp_adapter_returns_verified_access_token_and_hides_invalid_tokens() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = _verifier(private_key.public_key())
    adapter = McpAuth0TokenVerifier(verifier, "https://mcp.storecipe.example/mcp")
    token = _token(
        private_key,
        audience=[
            "https://api.storecipe.example",
            "https://mcp.storecipe.example/mcp",
        ],
    )

    access_token = await adapter.verify_token(token)
    invalid = await adapter.verify_token("not-a-jwt")

    assert isinstance(access_token, AccessToken)
    assert access_token.token == token
    assert access_token.subject == "auth0|user-123"
    assert access_token.client_id == "auth0|user-123"
    assert access_token.scopes == ["recipes:read", "recipes:write"]
    assert access_token.resource == "https://mcp.storecipe.example/mcp"
    assert invalid is None


@pytest.mark.asyncio
async def test_mcp_adapter_rejects_audience_without_its_resource() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = _verifier(private_key.public_key())
    adapter = McpAuth0TokenVerifier(verifier, "https://mcp.storecipe.example/mcp")

    token = _token(private_key, audience="https://api.storecipe.example")

    assert await adapter.verify_token(token) is None


def test_oauth_challenge_supports_mcp_scope_and_safe_error_metadata() -> None:
    settings = Settings(mcp_resource_url="https://mcp.storecipe.example/mcp")

    challenge = oauth_challenge(
        settings,
        "recipes:write",
        error="insufficient_scope",
        error_description="The access token lacks a required scope.",
    )

    assert challenge == (
        'Bearer resource_metadata="https://mcp.storecipe.example/'
        '.well-known/oauth-protected-resource/mcp", '
        'error="insufficient_scope", '
        'error_description="The access token lacks a required scope.", '
        'scope="recipes:write"'
    )
