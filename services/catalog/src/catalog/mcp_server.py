from typing import Any

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from catalog.auth import Auth0TokenVerifier, InvalidAccessToken
from catalog.config import Settings


class McpAuth0TokenVerifier(TokenVerifier):
    """Adapt the Catalog's Auth0 verifier to the MCP SDK verifier protocol."""

    def __init__(self, verifier: Auth0TokenVerifier, resource_url: str) -> None:
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


def create_mcp_server(settings: Settings, verifier: Auth0TokenVerifier) -> FastMCP[Any]:
    # Pydantic requires absolute URLs for discovery metadata. The invalid fallback
    # remains local: the token verifier still fails closed while
    # Auth0 configuration is absent.
    issuer = settings.auth0_issuer or "https://auth.invalid/"
    server = FastMCP(
        name="Storecipe Catalog",
        instructions="Read-only access to the authenticated user's Storecipe catalog.",
        token_verifier=McpAuth0TokenVerifier(verifier, settings.mcp_resource_url),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(settings.mcp_resource_url),
            required_scopes=["recipes:read"],
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={
            "securitySchemes": [
                {"type": "oauth2", "scopes": ["recipes:read"]},
            ]
        },
    )
    async def describe_storecipe() -> dict[str, Any]:
        """Describe the authenticated Storecipe MCP capability."""
        return {
            "service": "Storecipe",
            "access": "authenticated read-only catalog",
            "status": "available",
        }

    return server
