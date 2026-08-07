from collections.abc import Callable, Mapping
from typing import Any, cast
from uuid import UUID

import pytest
from mcp.server.auth.provider import AccessToken
from mcp.types import CallToolResult

import storecipe_mcp.auth as mcp_auth
from storecipe_mcp.auth import McpInboundTokenVerifier
from storecipe_mcp.config import Settings
from storecipe_mcp.errors import CatalogClientError
from storecipe_mcp.mcp_server import create_mcp_server
from storecipe_mcp.models import (
    IngredientCreate,
    IngredientView,
    RatingView,
    RecipeCreate,
    RecipeQueryPage,
    RecipeQueryRequest,
    RecipeView,
)

RECIPE_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
MCP_TOKEN = "verified-mcp-token"
API_TOKEN = "exchanged-api-token"


def _recipe_view() -> RecipeView:
    return RecipeView(
        id=RECIPE_ID,
        title="Tomato soup",
        source_url=None,
        servings=2,
        prep_minutes=10,
        cook_minutes=20,
        total_minutes=30,
        ingredients=[IngredientView(raw_text="2 tomatoes", name="tomato", quantity=2, unit=None)],
        instructions=["Cook the tomatoes."],
        tags=["soup"],
        rating=None,
    )


def _recipe_create() -> RecipeCreate:
    return RecipeCreate(
        title="Tomato soup",
        source_url=None,
        servings=2,
        prep_minutes=10,
        cook_minutes=20,
        total_minutes=30,
        ingredients=[IngredientCreate(raw_text="2 tomatoes", name="tomato", quantity=2, unit=None)],
        instructions=["Cook the tomatoes."],
        tags=["soup"],
    )


class RecordingCatalog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def query_recipes(self, query: RecipeQueryRequest, token: str) -> RecipeQueryPage:
        self.calls.append(("query_recipes", (query, token)))
        return RecipeQueryPage(items=[], next_cursor=None)

    async def get_recipe(self, recipe_id: UUID, token: str) -> RecipeView:
        self.calls.append(("get_recipe", (recipe_id, token)))
        return _recipe_view()

    async def create_recipe(
        self, payload: RecipeCreate, idempotency_key: str, token: str
    ) -> RecipeView:
        self.calls.append(("create_recipe", (payload, idempotency_key, token)))
        return _recipe_view()

    async def rate_recipe(self, recipe_id: UUID, value: int, token: str) -> RatingView:
        self.calls.append(("rate_recipe", (recipe_id, value, token)))
        return RatingView(value=value)


class FakeOboProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.invalidated: list[str] = []

    async def get_api_token(self, subject_token: str) -> str:
        self.calls.append(subject_token)
        if subject_token != MCP_TOKEN:
            raise CatalogClientError("authentication_required", retryable=False)
        return API_TOKEN

    async def invalidate(self, subject_token: str) -> None:
        self.invalidated.append(subject_token)


def _server(
    settings: Settings,
    catalog: object,
    obo_provider: FakeOboProvider | None = None,
):
    provider = obo_provider or FakeOboProvider()
    return create_mcp_server(
        settings,
        McpInboundTokenVerifier(settings),
        catalog_client_provider=cast(Callable[[], Any], lambda: catalog),
        obo_provider_factory=lambda: provider,
    )


def _access_token(*scopes: str) -> AccessToken:
    return AccessToken(
        token=MCP_TOKEN,
        client_id="mcp-client",
        subject="auth0|chef",
        scopes=list(scopes),
    )


def _property_names(schema: object) -> set[str]:
    if isinstance(schema, Mapping):
        names: set[str] = set()
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            names.update(str(name) for name in properties)
        for value in schema.values():
            names.update(_property_names(value))
        return names
    if isinstance(schema, list):
        names = set()
        for value in schema:
            names.update(_property_names(value))
        return names
    return set()


@pytest.mark.asyncio
async def test_gateway_exposes_exact_typed_tools_with_approved_contracts(
    settings: Settings,
) -> None:
    server = _server(settings, RecordingCatalog())

    tools = {tool.name: tool for tool in await server.list_tools()}

    assert set(tools) == {"query_recipes", "get_recipe", "create_recipe", "rate_recipe"}
    assert set(server._tool_scopes) == set(tools)
    assert tools["query_recipes"].inputSchema["properties"].keys() == {"request"}
    assert tools["get_recipe"].inputSchema["properties"].keys() == {"recipe_id"}
    assert tools["create_recipe"].inputSchema["properties"].keys() == {
        "idempotency_key",
        "recipe",
    }
    assert tools["rate_recipe"].inputSchema["properties"].keys() == {"recipe_id", "value"}

    expected_scopes = {
        "query_recipes": "recipes:read",
        "get_recipe": "recipes:read",
        "create_recipe": "recipes:write",
        "rate_recipe": "ratings:write",
    }
    expected_annotations = {
        "query_recipes": (True, False, True, False),
        "get_recipe": (True, False, True, False),
        "create_recipe": (False, False, True, False),
        "rate_recipe": (False, True, True, False),
    }
    expected_outputs = {
        "query_recipes": RecipeQueryPage,
        "get_recipe": RecipeView,
        "create_recipe": RecipeView,
        "rate_recipe": RatingView,
    }
    for name, tool in tools.items():
        assert tool.meta == {
            "securitySchemes": [{"type": "oauth2", "scopes": [expected_scopes[name]]}]
        }
        assert tool.annotations is not None
        assert (
            tool.annotations.readOnlyHint,
            tool.annotations.destructiveHint,
            tool.annotations.idempotentHint,
            tool.annotations.openWorldHint,
        ) == expected_annotations[name]
        assert tool.outputSchema is not None
        assert tool.outputSchema == expected_outputs[name].model_json_schema()

    forbidden_argument_names = {
        "user_id",
        "subject",
        "identity",
        "token",
        "access_token",
        "base_url",
        "catalog_url",
        "internal_api_key",
    }
    for tool in tools.values():
        assert _property_names(tool.inputSchema).isdisjoint(forbidden_argument_names)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments", "expected_method"),
    [
        ("query_recipes", {"request": {}}, "query_recipes"),
        (
            "get_recipe",
            {"recipe_id": str(RECIPE_ID), "token": "attacker-supplied"},
            "get_recipe",
        ),
        (
            "create_recipe",
            {
                "idempotency_key": "idem-key-1",
                "recipe": _recipe_create().model_dump(mode="json", by_alias=True),
            },
            "create_recipe",
        ),
        (
            "rate_recipe",
            {"recipe_id": str(RECIPE_ID), "value": 4},
            "rate_recipe",
        ),
    ],
)
async def test_tools_exchange_mcp_token_and_forward_api_token_to_catalog(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    arguments: dict[str, Any],
    expected_method: str,
) -> None:
    catalog = RecordingCatalog()
    obo_provider = FakeOboProvider()
    server = _server(settings, catalog, obo_provider)
    monkeypatch.setattr(
        mcp_auth,
        "get_access_token",
        lambda: _access_token(
            *{
                "query_recipes": ["recipes:read"],
                "get_recipe": ["recipes:read"],
                "create_recipe": ["recipes:write"],
                "rate_recipe": ["ratings:write"],
            }[name]
        ),
    )

    result = await server.call_tool(name, arguments)

    assert isinstance(result, tuple)
    assert len(catalog.calls) == 1
    method, recorded_arguments = catalog.calls[0]
    assert method == expected_method
    assert recorded_arguments[-1] == API_TOKEN
    assert MCP_TOKEN not in recorded_arguments
    assert "attacker-supplied" not in repr(recorded_arguments)
    assert obo_provider.calls == [MCP_TOKEN]


@pytest.mark.asyncio
async def test_catalog_401_invalidates_cached_exchange_and_retries_once(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnauthorizedThenOkCatalog(RecordingCatalog):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def get_recipe(self, recipe_id: UUID, token: str) -> RecipeView:
            self.attempts += 1
            self.calls.append(("get_recipe", (recipe_id, token)))
            if self.attempts == 1:
                raise CatalogClientError("authentication_required", retryable=False)
            return _recipe_view()

    catalog = UnauthorizedThenOkCatalog()
    obo_provider = FakeOboProvider()
    server = _server(settings, catalog, obo_provider)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:read"))

    result = await server.call_tool("get_recipe", {"recipe_id": str(RECIPE_ID)})

    assert isinstance(result, tuple)
    assert catalog.attempts == 2
    assert obo_provider.invalidated == [MCP_TOKEN]
    assert obo_provider.calls == [MCP_TOKEN, MCP_TOKEN]
    assert all(arguments[-1] == API_TOKEN for _, arguments in catalog.calls)


@pytest.mark.asyncio
async def test_scope_denial_precedes_handler_conversion_and_upstream_call(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = RecordingCatalog()
    server = _server(settings, catalog)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:read"))

    result = await server.call_tool(
        "create_recipe",
        {"unexpected": object(), "recipe": {"secret": "must-not-convert"}},
    )

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert len(catalog.calls) == 0
    assert result.meta == {
        "mcp/www_authenticate": [
            'Bearer resource_metadata="https://mcp.storecipe.example/.well-known/'
            'oauth-protected-resource/mcp", error="insufficient_scope", '
            'error_description="The access token lacks a required scope.", '
            'scope="recipes:write"'
        ]
    }


@pytest.mark.asyncio
async def test_catalog_error_is_unwrapped_and_translated_without_cause_leak(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCatalog(RecordingCatalog):
        async def get_recipe(self, recipe_id: UUID, token: str) -> RecipeView:
            raise CatalogClientError("temporary_catalog_failure", retryable=True) from RuntimeError(
                "secret upstream body and token"
            )

    server = _server(settings, FailingCatalog())
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:read"))

    result = await server.call_tool("get_recipe", {"recipe_id": str(RECIPE_ID)})

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.content[0].text == "Catalog is temporarily unavailable."
    assert "secret upstream body" not in result.content[0].text
    assert "temporary_catalog_failure" not in result.content[0].text


@pytest.mark.asyncio
async def test_authorized_malformed_arguments_return_fixed_safe_error(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = RecordingCatalog()
    server = _server(settings, catalog)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:read"))

    result = await server.call_tool(
        "get_recipe",
        {"recipe_id": "not-a-uuid", "secret": "pydantic-input-secret"},
    )

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.content[0].text == "The tool request could not be processed."
    assert "pydantic-input-secret" not in result.content[0].text
    assert len(catalog.calls) == 0


@pytest.mark.asyncio
async def test_unexpected_catalog_exception_returns_fixed_safe_error(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingCatalog(RecordingCatalog):
        async def get_recipe(self, recipe_id: UUID, token: str) -> RecipeView:
            raise RuntimeError("secret unexpected cause")

    server = _server(settings, ExplodingCatalog())
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:read"))

    result = await server.call_tool("get_recipe", {"recipe_id": str(RECIPE_ID)})

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.content[0].text == "The tool request could not be processed."
    assert "secret unexpected cause" not in result.content[0].text


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_before_dispatch(
    settings: Settings,
) -> None:
    server = _server(settings, RecordingCatalog())

    result = await server.call_tool("not_registered", {"secret": object()})

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.content[0].text == "The requested tool is not available."


@pytest.mark.asyncio
async def test_typed_success_preserves_structured_output(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = RecordingCatalog()
    server = _server(settings, catalog)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:read"))

    result = await server.call_tool("query_recipes", {"request": {}})

    assert isinstance(result, tuple)
    assert result[1] == {"items": [], "nextCursor": None}
