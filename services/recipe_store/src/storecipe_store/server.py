import hashlib
import os
import pwd
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from storecipe_store.config import Settings, load_settings
from storecipe_store.models import (
    CatalogOverview,
    DeleteResult,
    IngredientInput,
    RecipeRecord,
    SearchResult,
)
from storecipe_store.store import (
    InvalidRecipe,
    RecipeDraft,
    RecipeNotFound,
    RecipeStore,
    RecipeUpdate,
    SearchQuery,
    parse_recipe_id,
)

_INSTRUCTIONS = (
    "Personal recipe library stored on disk. There is one library and no accounts. "
    "Search before saying a recipe is missing. get_recipe returns the steps. "
    "save_recipe creates a recipe, or replaces that recipe when recipe_id is set. "
    "Replacing keeps rating, favorite, and last cooked time. "
    "update_recipe changes only the fields you pass. "
    "Answer from tool results and do not invent recipes that are not stored."
)
_READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


class BearerGate:
    """Require Authorization: Bearer on every route except the health check."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = hashlib.sha256(f"Bearer {token}".encode()).digest()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return
        header = _authorization_header(scope)
        actual = hashlib.sha256(header.encode()).digest()
        if not secrets.compare_digest(self._expected, actual):
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def create_server(settings: Settings, store: RecipeStore) -> FastMCP[Any]:
    security = None
    if settings.token is not None:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp: FastMCP[Any] = FastMCP(
        name="Storecipe",
        instructions=_INSTRUCTIONS,
        host=settings.host,
        port=settings.port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=security,
    )
    _register_tools(mcp, store)
    return mcp


def create_http_app(settings: Settings, store: RecipeStore) -> ASGIApp:
    mcp = create_server(settings, store)

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    mcp.custom_route("/health", methods=["GET"])(health)

    app = mcp.streamable_http_app()
    if settings.token is not None:
        app.add_middleware(BearerGate, token=settings.token)
    return app


def main() -> None:
    settings = load_settings()
    _drop_privileges(settings.data_path.parent)
    store = RecipeStore(settings.data_path)
    if settings.transport == "stdio":
        create_server(settings, store).run(transport="stdio")
        return
    uvicorn.run(
        create_http_app(settings, store),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


def _register_tools(mcp: FastMCP[Any], store: RecipeStore) -> None:
    @mcp.tool(annotations=_READ)
    def search_recipes(
        query: Annotated[
            str | None,
            Field(description="Words matched against title, ingredients, steps, tags, and notes."),
        ] = None,
        ingredients: Annotated[
            list[str] | None,
            Field(description="Every ingredient is required. Matching is by whole word."),
        ] = None,
        tags: Annotated[
            list[str] | None,
            Field(description="Every tag is required."),
        ] = None,
        max_total_minutes: Annotated[int | None, Field(ge=0, le=100_000)] = None,
        favorite_only: bool = False,
        min_rating: Annotated[int | None, Field(ge=1, le=5)] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> SearchResult:
        """Search the library. An empty search lists the most recently updated recipes.

        Results are summaries. Call get_recipe before quoting steps or amounts.
        """

        search = SearchQuery(
            text=query,
            ingredients=ingredients or [],
            tags=tags or [],
            max_total_minutes=max_total_minutes,
            favorite_only=favorite_only,
            min_rating=min_rating,
            limit=limit,
        )
        return _run(lambda: store.search(search))

    @mcp.tool(annotations=_READ)
    def get_recipe(recipe_id: str) -> RecipeRecord:
        """Get one stored recipe, including ingredients, steps, rating, and notes."""

        return _run(lambda: store.get(recipe_id))

    @mcp.tool(annotations=_WRITE)
    def save_recipe(
        title: Annotated[str, Field(min_length=1, max_length=200)],
        ingredients: Annotated[list[IngredientInput], Field(min_length=1, max_length=256)],
        instructions: Annotated[list[str], Field(min_length=1, max_length=256)],
        recipe_id: Annotated[
            str | None,
            Field(description="Existing id to replace. Omit to create a new recipe."),
        ] = None,
        description: Annotated[str, Field(max_length=2_000)] = "",
        source_url: str | None = None,
        servings: Annotated[int | None, Field(ge=1, le=1_000)] = None,
        prep_minutes: Annotated[int | None, Field(ge=0, le=100_000)] = None,
        cook_minutes: Annotated[int | None, Field(ge=0, le=100_000)] = None,
        total_minutes: Annotated[int | None, Field(ge=0, le=100_000)] = None,
        tags: Annotated[list[str] | None, Field(max_length=64)] = None,
        personal_notes: Annotated[str | None, Field(max_length=5_000)] = None,
    ) -> RecipeRecord:
        """Create a recipe, or replace that recipe when recipe_id already exists.

        A replacement keeps the rating, favorite flag, and last cooked time.
        personal_notes is left unchanged unless you pass it.
        """

        draft = RecipeDraft(
            title=title,
            ingredients=ingredients,
            instructions=instructions,
            description=description,
            source_url=source_url,
            servings=servings,
            prep_minutes=prep_minutes,
            cook_minutes=cook_minutes,
            total_minutes=total_minutes,
            tags=tags or [],
            personal_notes=personal_notes,
            notes_included=personal_notes is not None,
        )
        return _run(lambda: store.save(draft, recipe_id))

    @mcp.tool(annotations=_WRITE)
    def update_recipe(
        recipe_id: str,
        title: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        description: Annotated[str | None, Field(max_length=2_000)] = None,
        source_url: str | None = None,
        clear_source_url: bool = False,
        servings: Annotated[int | None, Field(ge=1, le=1_000)] = None,
        prep_minutes: Annotated[int | None, Field(ge=0, le=100_000)] = None,
        cook_minutes: Annotated[int | None, Field(ge=0, le=100_000)] = None,
        total_minutes: Annotated[int | None, Field(ge=0, le=100_000)] = None,
        ingredients: Annotated[
            list[IngredientInput] | None, Field(min_length=1, max_length=256)
        ] = None,
        instructions: Annotated[list[str] | None, Field(min_length=1, max_length=256)] = None,
        tags: Annotated[list[str] | None, Field(max_length=64)] = None,
        personal_notes: Annotated[str | None, Field(max_length=5_000)] = None,
        clear_notes: bool = False,
        rating: Annotated[int | None, Field(ge=1, le=5)] = None,
        clear_rating: bool = False,
        favorite: bool | None = None,
        mark_cooked: bool = False,
    ) -> RecipeRecord:
        """Change only the fields you pass. Omitted fields stay as they are.

        Use clear_notes, clear_rating, or clear_source_url to remove those values.
        mark_cooked sets last cooked time to now.
        """

        if clear_rating and rating is not None:
            raise ValueError("Pass rating or clear_rating, not both")
        if clear_notes and personal_notes is not None:
            raise ValueError("Pass personal_notes or clear_notes, not both")
        if clear_source_url and source_url is not None:
            raise ValueError("Pass source_url or clear_source_url, not both")
        changes: RecipeUpdate = {}
        if title is not None:
            changes["title"] = title
        if description is not None:
            changes["description"] = description
        if clear_source_url:
            changes["source_url"] = None
        elif source_url is not None:
            changes["source_url"] = source_url
        if servings is not None:
            changes["servings"] = servings
        if prep_minutes is not None:
            changes["prep_minutes"] = prep_minutes
        if cook_minutes is not None:
            changes["cook_minutes"] = cook_minutes
        if total_minutes is not None:
            changes["total_minutes"] = total_minutes
        if ingredients is not None:
            changes["ingredients"] = ingredients
        if instructions is not None:
            changes["instructions"] = instructions
        if tags is not None:
            changes["tags"] = tags
        if clear_notes:
            changes["personal_notes"] = None
        elif personal_notes is not None:
            changes["personal_notes"] = personal_notes
        if clear_rating:
            changes["rating"] = None
        elif rating is not None:
            changes["rating"] = rating
        if favorite is not None:
            changes["favorite"] = favorite
        if mark_cooked:
            changes["mark_cooked"] = True
        return _run(lambda: store.update(recipe_id, changes))

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def delete_recipe(recipe_id: str) -> DeleteResult:
        """Delete one recipe. Deleting an unknown id leaves the library unchanged."""

        def delete() -> DeleteResult:
            canonical = parse_recipe_id(recipe_id)
            return DeleteResult(recipe_id=canonical, deleted=store.delete(canonical))

        return _run(delete)

    @mcp.tool(annotations=_READ)
    def catalog_overview() -> CatalogOverview:
        """Summarize the library: counts, average rating, time buckets, tags, and ingredients.

        Tag and ingredient lists are capped at 40. Use search_recipes to look further.
        """

        return _run(store.overview)


def _drop_privileges(data_dir: Path) -> None:
    """When the container starts as root, own the data directory as the app user."""

    if os.geteuid() != 0:
        return
    try:
        account = pwd.getpwnam("app")
    except KeyError:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chown(data_dir, account.pw_uid, account.pw_gid)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)


def _run[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except RecipeNotFound as exc:
        raise ValueError(f"No recipe with id {exc.recipe_id}") from exc
    except InvalidRecipe as exc:
        raise ValueError(exc.message) from exc


def _authorization_header(scope: Scope) -> str:
    raw_headers = scope.get("headers", [])
    if not isinstance(raw_headers, list):
        return ""
    for item in raw_headers:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        name, value = item
        if name == b"authorization" and isinstance(value, bytes):
            return value.decode("latin-1")
    return ""
