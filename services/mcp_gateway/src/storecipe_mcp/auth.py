from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token as _get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier

from storecipe_auth import Auth0TokenVerifier, InvalidAccessToken, Principal
from storecipe_mcp.config import Settings

__all__ = [
    "Auth0TokenVerifier",
    "InvalidAccessToken",
    "McpAuth0TokenVerifier",
    "McpInboundTokenVerifier",
    "McpTokenVerifier",
    "Principal",
    "get_access_token",
    "oauth_challenge",
]


@dataclass(frozen=True)
class _JwtSettings:
    auth0_issuer: str
    resolved_jwks_url: str


class McpInboundTokenVerifier:
    """Validate inbound MCP access tokens against the canonical MCP resource audience."""

    def __init__(self, settings: Settings, *, jwk_client: object | None = None) -> None:
        self._settings = settings
        self._verifier = Auth0TokenVerifier(
            _JwtSettings(
                auth0_issuer=settings.auth0_issuer,
                resolved_jwks_url=settings.resolved_jwks_url,
            ),
            audience=settings.mcp_resource_url,
            jwk_client=jwk_client,
        )

    async def verify(self, token: str) -> Principal:
        return await self._verifier.verify(token)


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
