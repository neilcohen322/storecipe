from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import ConfigDict, Field

from catalog.auth import Principal, require_scopes
from catalog.database import SessionDependency
from catalog.recipe_queries import RecipeQueryPage, RecipeQueryRequest
from catalog.schemas import RecipeCreate, RecipePatch, RecipeView
from catalog.services import recipe_queries as recipe_query_service
from catalog.services import recipes as recipe_service

router = APIRouter(prefix="/v1/recipes", tags=["recipes"])

ReadPrincipal = Annotated[Principal, Depends(require_scopes("recipes:read"))]
WritePrincipal = Annotated[Principal, Depends(require_scopes("recipes:write"))]
RecipeCreateIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


class RecipeQueryParameters(RecipeQueryRequest):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: Annotated[str | None, Field(max_length=200)] = None
    required_ingredients: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=32, alias="requiredIngredient"
    )
    available_ingredients: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=64, alias="availableIngredient"
    )
    required_tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=16, alias="requiredTag"
    )
    preferred_tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=16, alias="preferredTag"
    )
    max_total_minutes: int | None = Field(default=None, ge=0, alias="maxTotalMinutes")
    min_rating: int | None = Field(default=None, ge=1, le=5, alias="minRating")
    rating_state: Literal["any", "rated", "unrated"] = Field(default="any", alias="ratingState")
    sort: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=6
    )
    cursor: Annotated[str | None, Field(max_length=1024)] = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20


@router.post("", response_model=RecipeView, status_code=status.HTTP_201_CREATED)
async def create_recipe(
    payload: RecipeCreate,
    response: Response,
    session: SessionDependency,
    principal: WritePrincipal,
    idempotency_key: RecipeCreateIdempotencyKey,
) -> RecipeView:
    view, replayed = await recipe_service.create_recipe_idempotently(
        session, principal.subject, idempotency_key, payload
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    return view


@router.get("", response_model=RecipeQueryPage)
async def query_recipes(
    request: Request,
    session: SessionDependency,
    principal: ReadPrincipal,
    parameters: Annotated[RecipeQueryParameters, Query()],
) -> RecipeQueryPage:
    query = RecipeQueryRequest.model_validate(parameters.model_dump())
    return await recipe_query_service.query_recipes(
        session, principal.subject, query, request.app.state.recipe_query_cache
    )


@router.get("/{recipe_id}", response_model=RecipeView)
async def get_recipe(
    recipe_id: UUID,
    session: SessionDependency,
    principal: ReadPrincipal,
) -> RecipeView:
    return await recipe_service.get_recipe(session, principal.subject, recipe_id)


@router.patch("/{recipe_id}", response_model=RecipeView)
async def update_recipe(
    recipe_id: UUID,
    payload: RecipePatch,
    session: SessionDependency,
    principal: WritePrincipal,
) -> RecipeView:
    return await recipe_service.update_recipe(session, principal.subject, recipe_id, payload)


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(
    recipe_id: UUID,
    session: SessionDependency,
    principal: WritePrincipal,
) -> Response:
    await recipe_service.delete_recipe(session, principal.subject, recipe_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
