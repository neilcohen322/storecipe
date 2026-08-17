from collections.abc import Callable, Mapping
from typing import Any, cast
from uuid import UUID

import pytest
from mcp.server.auth.provider import AccessToken
from mcp.types import CallToolResult

import storecipe_mcp.auth as mcp_auth
from storecipe_mcp.auth import McpInboundTokenVerifier
from storecipe_mcp.config import Settings
from storecipe_mcp.errors import CatalogClientError, IngestionClientError
from storecipe_mcp.mcp_server import create_mcp_server
from storecipe_mcp.models import (
    CatalogRecipeCreate,
    IngredientCreate,
    IngredientDraft,
    IngredientNormalizationRequest,
    IngredientNormalizationResponse,
    IngredientView,
    RatingView,
    RecipeCreate,
    RecipeFacetBounds,
    RecipeFacetBrowseRequest,
    RecipeFacetPage,
    RecipeFacetSelectionsRequest,
    RecipeFacetSelectionsResponse,
    RecipeQueryPage,
    RecipeQueryRequest,
    RecipeView,
)

RECIPE_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
MCP_TOKEN = "verified-mcp-token"
API_TOKEN = "exchanged-api-token"
SECRET_INGREDIENT = "secret-ingredient-marker"


def _recipe_view() -> RecipeView:
    return RecipeView(
        id=RECIPE_ID,
        title="Tomato soup",
        source_url=None,
        servings=2,
        prep_minutes=10,
        cook_minutes=20,
        total_minutes=30,
        ingredients=[
            IngredientView(
                raw_text="2 tomatoes",
                name="tomato",
                canonical_name="tomato",
                quantity=2,
                unit=None,
            )
        ],
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
        ingredients=[IngredientDraft(raw_text=SECRET_INGREDIENT)],
        instructions=["Cook the tomatoes."],
        tags=["soup"],
    )


def _normalized_ingredients() -> list[IngredientCreate]:
    return [
        IngredientCreate(
            raw_text=SECRET_INGREDIENT,
            name="tomato",
            canonical_name="tomato",
            quantity=2,
            unit=None,
        )
    ]


def _facet_page() -> RecipeFacetPage:
    return RecipeFacetPage(
        ingredients=[],
        ingredient_next_cursor=None,
        tags=[],
        tag_next_cursor=None,
        total_minutes=None,
        rating=RecipeFacetBounds(min=1, max=5),
        rating_state=["any", "rated", "unrated"],
        sort=[
            "rating:asc",
            "rating:desc",
            "totalMinutes:asc",
            "totalMinutes:desc",
            "createdAt:asc",
            "createdAt:desc",
            "updatedAt:asc",
            "updatedAt:desc",
            "title:asc",
            "title:desc",
        ],
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
        self, payload: CatalogRecipeCreate, idempotency_key: str, token: str
    ) -> RecipeView:
        self.calls.append(("create_recipe", (payload, idempotency_key, token)))
        return _recipe_view()

    async def rate_recipe(self, recipe_id: UUID, value: int, token: str) -> RatingView:
        self.calls.append(("rate_recipe", (recipe_id, value, token)))
        return RatingView(value=value)

    async def list_recipe_query_options(
        self, request: RecipeFacetBrowseRequest, token: str
    ) -> RecipeFacetPage:
        self.calls.append(("list_recipe_query_options", (request, token)))
        return _facet_page()

    async def resolve_recipe_query_selections(
        self, request: RecipeFacetSelectionsRequest, token: str
    ) -> RecipeFacetSelectionsResponse:
        self.calls.append(("resolve_recipe_query_selections", (request, token)))
        return RecipeFacetSelectionsResponse(ingredients=[], tags=[])


class RecordingIngestion:
    def __init__(
        self,
        *,
        response: IngredientNormalizationResponse | None = None,
        error: IngestionClientError | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._response = response or IngredientNormalizationResponse(
            ingredients=_normalized_ingredients()
        )
        self._error = error

    async def normalize_ingredients(
        self,
        request: IngredientNormalizationRequest,
        idempotency_key: str,
        token: str,
    ) -> IngredientNormalizationResponse:
        self.calls.append(("normalize_ingredients", (request, idempotency_key, token)))
        if self._error is not None:
            raise self._error
        return self._response


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
    *,
    ingestion: object | None = None,
    obo_provider: FakeOboProvider | None = None,
):
    provider = obo_provider or FakeOboProvider()
    ingestion_client = ingestion if ingestion is not None else RecordingIngestion()
    return create_mcp_server(
        settings,
        McpInboundTokenVerifier(settings),
        catalog_client_provider=cast(Callable[[], Any], lambda: catalog),
        ingestion_client_provider=cast(Callable[[], Any], lambda: ingestion_client),
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

    assert set(tools) == {
        "query_recipes",
        "get_recipe",
        "create_recipe",
        "rate_recipe",
        "list_recipe_query_options",
        "resolve_recipe_query_selections",
    }
    assert set(server._tool_scopes) == set(tools)
    assert tools["query_recipes"].inputSchema["properties"].keys() == {"request"}
    assert tools["get_recipe"].inputSchema["properties"].keys() == {"recipe_id"}
    assert tools["create_recipe"].inputSchema["properties"].keys() == {
        "idempotency_key",
        "recipe",
    }
    assert tools["rate_recipe"].inputSchema["properties"].keys() == {"recipe_id", "value"}
    assert tools["list_recipe_query_options"].inputSchema["properties"].keys() == {"request"}
    assert tools["resolve_recipe_query_selections"].inputSchema["properties"].keys() == {"request"}

    expected_scopes = {
        "query_recipes": "recipes:read",
        "get_recipe": "recipes:read",
        "create_recipe": "recipes:write",
        "rate_recipe": "ratings:write",
        "list_recipe_query_options": "recipes:read",
        "resolve_recipe_query_selections": "recipes:read",
    }
    expected_annotations = {
        "query_recipes": (True, False, True, False),
        "get_recipe": (True, False, True, False),
        "create_recipe": (False, False, True, False),
        "rate_recipe": (False, True, True, False),
        "list_recipe_query_options": (True, False, True, False),
        "resolve_recipe_query_selections": (True, False, True, False),
    }
    expected_outputs = {
        "query_recipes": RecipeQueryPage,
        "get_recipe": RecipeView,
        "create_recipe": RecipeView,
        "rate_recipe": RatingView,
        "list_recipe_query_options": RecipeFacetPage,
        "resolve_recipe_query_selections": RecipeFacetSelectionsResponse,
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
        assert "legal" not in (tool.description or "").lower()

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

    query_names = _property_names(tools["query_recipes"].inputSchema)
    assert "ingredient" in query_names
    assert "tag" in query_names
    assert query_names.isdisjoint(
        {
            "requiredIngredient",
            "availableIngredient",
            "requiredTag",
            "preferredTag",
        }
    )
    query_schema_text = str(tools["query_recipes"].inputSchema)
    assert "ingredientCoverage" not in query_schema_text
    assert "tagCoverage" not in query_schema_text
    assert "Every listed value is required (AND)." in (tools["query_recipes"].description or "")
    facet_sort = tools["list_recipe_query_options"].outputSchema
    assert "requiresAvailableIngredient" not in str(facet_sort)
    assert "requiresPreferredTag" not in str(facet_sort)
    assert "unconditional" not in str(facet_sort)


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
        ("list_recipe_query_options", {"request": {}}, "list_recipe_query_options"),
        ("resolve_recipe_query_selections", {"request": {}}, "resolve_recipe_query_selections"),
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
    ingestion = RecordingIngestion()
    obo_provider = FakeOboProvider()
    server = _server(settings, catalog, ingestion=ingestion, obo_provider=obo_provider)
    monkeypatch.setattr(
        mcp_auth,
        "get_access_token",
        lambda: _access_token(
            *{
                "query_recipes": ["recipes:read"],
                "get_recipe": ["recipes:read"],
                "create_recipe": ["recipes:write"],
                "rate_recipe": ["ratings:write"],
                "list_recipe_query_options": ["recipes:read"],
                "resolve_recipe_query_selections": ["recipes:read"],
            }[name]
        ),
    )

    result = await server.call_tool(name, arguments)

    assert isinstance(result, tuple)
    if name == "create_recipe":
        assert len(catalog.calls) == 1
        assert len(ingestion.calls) == 1
        ingestion_method, ingestion_arguments = ingestion.calls[0]
        catalog_method, catalog_arguments = catalog.calls[0]
        assert ingestion_method == "normalize_ingredients"
        assert catalog_method == "create_recipe"
        assert ingestion_arguments[1] == catalog_arguments[1] == "idem-key-1"
        assert ingestion_arguments[-1] == API_TOKEN
        assert catalog_arguments[-1] == API_TOKEN
        assert catalog_arguments[0].ingredients[0].canonical_name == "tomato"
    else:
        assert len(catalog.calls) == 1
        method, recorded_arguments = catalog.calls[0]
        assert method == expected_method
        assert recorded_arguments[-1] == API_TOKEN
    assert MCP_TOKEN not in repr(catalog.calls)
    assert "attacker-supplied" not in repr(catalog.calls)
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
    server = _server(settings, catalog, obo_provider=obo_provider)
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
    ingestion = RecordingIngestion()
    server = _server(settings, catalog, ingestion=ingestion)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:read"))

    result = await server.call_tool(
        "create_recipe",
        {"unexpected": object(), "recipe": {"secret": "must-not-convert"}},
    )

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert len(catalog.calls) == 0
    assert len(ingestion.calls) == 0
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
async def test_stale_recipe_facet_cursor_maps_to_fixed_message_without_body_leakage(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StaleFacetCatalog(RecordingCatalog):
        async def list_recipe_query_options(
            self, request: RecipeFacetBrowseRequest, token: str
        ) -> RecipeFacetPage:
            raise CatalogClientError(
                "stale_recipe_facet_cursor", retryable=False
            ) from RuntimeError("secret stale cursor body and token")

    server = _server(settings, StaleFacetCatalog())
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:read"))

    result = await server.call_tool("list_recipe_query_options", {"request": {}})

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.content[0].text == "The recipe facet cursor is stale."
    assert "secret stale cursor body" not in result.content[0].text
    assert "stale_recipe_facet_cursor" not in result.content[0].text


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


@pytest.mark.asyncio
async def test_query_recipes_rejects_duplicate_overflow_before_catalog_call(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = RecordingCatalog()
    server = _server(settings, catalog)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:read"))

    too_many_ingredients = await server.call_tool(
        "query_recipes",
        {"request": {"ingredient": ["egg"] * 33}},
    )
    too_many_tags = await server.call_tool(
        "query_recipes",
        {"request": {"tag": ["quick"] * 17}},
    )

    assert isinstance(too_many_ingredients, CallToolResult)
    assert isinstance(too_many_tags, CallToolResult)
    assert too_many_ingredients.isError is True
    assert too_many_tags.isError is True
    assert len(catalog.calls) == 0


@pytest.mark.asyncio
async def test_create_recipe_calls_ingestion_before_catalog_with_same_idempotency_key(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = RecordingCatalog()
    ingestion = RecordingIngestion()
    server = _server(settings, catalog, ingestion=ingestion)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:write"))

    result = await server.call_tool(
        "create_recipe",
        {
            "idempotency_key": "idem-key-1",
            "recipe": _recipe_create().model_dump(mode="json", by_alias=True),
        },
    )

    assert isinstance(result, tuple)
    assert len(ingestion.calls) == 1
    assert len(catalog.calls) == 1
    assert ingestion.calls[0][0] == "normalize_ingredients"
    assert catalog.calls[0][0] == "create_recipe"
    assert ingestion.calls[0][1][1] == catalog.calls[0][1][1] == "idem-key-1"
    assert catalog.calls[0][1][0].ingredients[0].canonical_name == "tomato"


@pytest.mark.asyncio
async def test_create_recipe_skips_catalog_when_ingestion_raises(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = RecordingCatalog()
    ingestion = RecordingIngestion(
        error=IngestionClientError("temporary_ingestion_failure", retryable=True)
    )
    server = _server(settings, catalog, ingestion=ingestion)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:write"))

    result = await server.call_tool(
        "create_recipe",
        {
            "idempotency_key": "idem-key-1",
            "recipe": _recipe_create().model_dump(mode="json", by_alias=True),
        },
    )

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert len(ingestion.calls) == 1
    assert len(catalog.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_category", "expected_message"),
    [
        ("authentication_required", "Authentication is required."),
        ("insufficient_scope", "Additional authorization is required."),
        ("invalid_input", "The request is invalid."),
        ("idempotency_conflict", "The idempotency key conflicts with an existing normalization."),
        ("ingestion_rate_limited", "Ingredient normalization is rate limited. Try again later."),
        ("ingredient_normalization_invalid_output", "Ingredient normalization failed."),
        ("temporary_ingestion_failure", "Ingredient normalization is temporarily unavailable."),
    ],
)
async def test_ingestion_errors_map_to_safe_messages_without_ingredient_leakage(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    status_category: str,
    expected_message: str,
) -> None:
    catalog = RecordingCatalog()
    ingestion = RecordingIngestion(
        error=IngestionClientError(
            status_category,
            retryable=status_category in {"ingestion_rate_limited", "temporary_ingestion_failure"},
            required_scope="recipes:write" if status_category == "insufficient_scope" else None,
        )
    )
    server = _server(settings, catalog, ingestion=ingestion)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:write"))

    result = await server.call_tool(
        "create_recipe",
        {
            "idempotency_key": "idem-key-1",
            "recipe": _recipe_create().model_dump(mode="json", by_alias=True),
        },
    )

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.content[0].text == expected_message
    assert SECRET_INGREDIENT not in result.content[0].text
    assert len(catalog.calls) == 0


@pytest.mark.asyncio
async def test_exact_ingestion_replay_still_proceeds_to_catalog(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = RecordingCatalog()
    ingestion = RecordingIngestion()
    server = _server(settings, catalog, ingestion=ingestion)
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: _access_token("recipes:write"))
    arguments = {
        "idempotency_key": "idem-key-1",
        "recipe": _recipe_create().model_dump(mode="json", by_alias=True),
    }

    first = await server.call_tool("create_recipe", arguments)
    second = await server.call_tool("create_recipe", arguments)

    assert isinstance(first, tuple)
    assert isinstance(second, tuple)
    assert len(ingestion.calls) == 2
    assert len(catalog.calls) == 2
