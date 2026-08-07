from collections.abc import Callable, Mapping
from typing import Annotated, Any, cast
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import AnyHttpUrl, Field

import storecipe_mcp.auth as mcp_auth
from storecipe_mcp.auth import McpInboundTokenVerifier
from storecipe_mcp.catalog_client import CatalogClient
from storecipe_mcp.config import Settings
from storecipe_mcp.errors import CatalogClientError
from storecipe_mcp.models import (
    RatingView,
    RecipeCreate,
    RecipeCreateIdempotencyKey,
    RecipeQueryPage,
    RecipeQueryRequest,
    RecipeView,
)
from storecipe_mcp.obo_client import OboTokenProvider

CatalogClientProvider = Callable[[], CatalogClient]
OboTokenProviderFactory = Callable[[], OboTokenProvider]

_READ_SCOPE = "recipes:read"
_WRITE_SCOPE = "recipes:write"
_RATING_SCOPE = "ratings:write"
_SCOPE_ERROR_DESCRIPTION = "The access token lacks a required scope."
_AUTH_ERROR_DESCRIPTION = "The access token is invalid or expired."
_UNAVAILABLE_TOOL_MESSAGE = "The requested tool is not available."
_UNEXPECTED_ERROR_MESSAGE = "The tool request could not be processed."


class GatewayFastMCP(FastMCP[Any]):
    def __init__(
        self,
        *args: Any,
        settings: Settings,
        tool_scopes: Mapping[str, str],
        **kwargs: Any,
    ) -> None:
        self._storecipe_settings = settings
        self._tool_scopes = dict(tool_scopes)
        super().__init__(*args, **kwargs)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Enforce the gateway scope before FastMCP converts tool arguments."""

        required_scope = self._tool_scopes.get(name)
        if required_scope is None:
            return _error_result(_UNAVAILABLE_TOOL_MESSAGE)
        access_token = mcp_auth.get_access_token()
        if access_token is None or required_scope not in access_token.scopes:
            return _scope_error_result(self._storecipe_settings, required_scope)

        try:
            return await super().call_tool(name, arguments)
        except Exception as exc:
            catalog_error = _find_catalog_error(exc)
            if catalog_error is not None:
                return _catalog_error_result(
                    self._storecipe_settings,
                    catalog_error,
                    required_scope=required_scope,
                )
            return _error_result(_UNEXPECTED_ERROR_MESSAGE)


def create_mcp_server(
    settings: Settings,
    verifier: McpInboundTokenVerifier,
    *,
    catalog_client_provider: CatalogClientProvider,
    obo_provider_factory: OboTokenProviderFactory,
) -> GatewayFastMCP:
    """Build the gateway MCP server and register its four public tools."""

    issuer = settings.auth0_issuer or "https://auth.invalid/"
    server = GatewayFastMCP(
        name="Storecipe MCP Gateway",
        settings=settings,
        tool_scopes={
            "query_recipes": _READ_SCOPE,
            "get_recipe": _READ_SCOPE,
            "create_recipe": _WRITE_SCOPE,
            "rate_recipe": _RATING_SCOPE,
        },
        instructions="Access the authenticated user's Storecipe recipe catalog.",
        token_verifier=mcp_auth.McpAuth0TokenVerifier(verifier, settings.mcp_resource_url),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(settings.mcp_resource_url),
            required_scopes=[],
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[_mcp_resource_host(settings)],
        ),
    )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={"securitySchemes": [{"type": "oauth2", "scopes": [_READ_SCOPE]}]},
    )
    async def query_recipes(request: RecipeQueryRequest) -> RecipeQueryPage:
        """Search recipes with explicit deterministic filters and ordered sorts."""

        return cast(
            RecipeQueryPage,
            await _call_catalog(
                catalog_client_provider,
                obo_provider_factory,
                "query_recipes",
                request,
            ),
        )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={"securitySchemes": [{"type": "oauth2", "scopes": [_READ_SCOPE]}]},
    )
    async def get_recipe(recipe_id: UUID) -> RecipeView:
        """Get one complete recipe from the authenticated user's catalog."""

        return cast(
            RecipeView,
            await _call_catalog(
                catalog_client_provider,
                obo_provider_factory,
                "get_recipe",
                recipe_id,
            ),
        )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={"securitySchemes": [{"type": "oauth2", "scopes": [_WRITE_SCOPE]}]},
    )
    async def create_recipe(
        idempotency_key: RecipeCreateIdempotencyKey,
        recipe: RecipeCreate,
    ) -> RecipeView:
        """Create a recipe with durable idempotency; sourceUrl is metadata only."""

        return cast(
            RecipeView,
            await _call_catalog(
                catalog_client_provider,
                obo_provider_factory,
                "create_recipe",
                recipe,
                idempotency_key,
            ),
        )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
        meta={"securitySchemes": [{"type": "oauth2", "scopes": [_RATING_SCOPE]}]},
    )
    async def rate_recipe(
        recipe_id: UUID,
        value: Annotated[int, Field(ge=1, le=5)],
    ) -> RatingView:
        """Set the authenticated user's 1-to-5 rating; an existing rating is replaced."""

        return cast(
            RatingView,
            await _call_catalog(
                catalog_client_provider,
                obo_provider_factory,
                "rate_recipe",
                recipe_id,
                value,
            ),
        )

    return server


def _verified_mcp_token() -> str:
    access_token = mcp_auth.get_access_token()
    if access_token is None or not isinstance(access_token.token, str) or not access_token.token:
        raise CatalogClientError("authentication_required", retryable=False)
    return access_token.token


async def _call_catalog(
    catalog_client_provider: CatalogClientProvider,
    obo_provider_factory: OboTokenProviderFactory,
    method_name: str,
    *args: Any,
) -> Any:
    catalog = catalog_client_provider()
    mcp_token = _verified_mcp_token()
    obo_provider = obo_provider_factory()
    api_token = await obo_provider.get_api_token(mcp_token)
    method = getattr(catalog, method_name)
    try:
        return await method(*args, api_token)
    except CatalogClientError as error:
        if error.category != "authentication_required":
            raise
        await obo_provider.invalidate(mcp_token)
        refreshed_token = await obo_provider.get_api_token(mcp_token)
        return await method(*args, refreshed_token)


def _mcp_resource_host(settings: Settings) -> str:
    parsed = urlsplit(settings.mcp_resource_url)
    host = parsed.hostname
    if host is None:
        raise ValueError("MCP_RESOURCE_URL must include a hostname")
    if ":" in host:
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return host


def _scope_error_result(settings: Settings, required_scope: str) -> CallToolResult:
    challenge = mcp_auth.oauth_challenge(
        settings,
        required_scopes=(required_scope,),
        error="insufficient_scope",
        error_description=_SCOPE_ERROR_DESCRIPTION,
    )
    return _error_result("Additional authorization is required.", challenge=challenge)


def _catalog_error_result(
    settings: Settings,
    error: CatalogClientError,
    *,
    required_scope: str | None,
) -> CallToolResult:
    if error.category == "authentication_required":
        scope_values: tuple[str, ...] = (required_scope,) if required_scope is not None else ()
        challenge = mcp_auth.oauth_challenge(
            settings,
            required_scopes=scope_values,
            error="invalid_token",
            error_description=_AUTH_ERROR_DESCRIPTION,
        )
        return _error_result("Authentication is required.", challenge=challenge)

    if error.category == "insufficient_scope":
        scope_value = error.required_scope or required_scope
        if scope_value is not None:
            challenge = mcp_auth.oauth_challenge(
                settings,
                required_scopes=(scope_value,),
                error="insufficient_scope",
                error_description=_SCOPE_ERROR_DESCRIPTION,
            )
            return _error_result("Additional authorization is required.", challenge=challenge)
        return _error_result("Additional authorization is required.")

    messages = {
        "invalid_input": "The request is invalid.",
        "invalid_query": "The recipe query is invalid.",
        "recipe_not_found": "Recipe not found.",
        "idempotency_conflict": "The idempotency key conflicts with an existing recipe.",
        "stale_recipe_query_cursor": "The recipe query cursor is stale.",
        "catalog_rate_limited": "Catalog is rate limited. Try again later.",
        "temporary_catalog_failure": "Catalog is temporarily unavailable.",
    }
    return _error_result(messages.get(error.category, messages["temporary_catalog_failure"]))


def _error_result(message: str, *, challenge: str | None = None) -> CallToolResult:
    meta = {"mcp/www_authenticate": [challenge]} if challenge is not None else None
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        isError=True,
        _meta=meta,
    )


def _find_catalog_error(exc: BaseException) -> CatalogClientError | None:
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        if isinstance(current, CatalogClientError):
            return current
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None
