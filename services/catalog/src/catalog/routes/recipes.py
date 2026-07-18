from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from catalog.auth import Principal, require_scopes
from catalog.database import SessionDependency
from catalog.schemas import RecipeCreate, RecipePage, RecipePatch, RecipeView
from catalog.services import recipes as recipe_service

router = APIRouter(prefix="/v1/recipes", tags=["recipes"])

ReadPrincipal = Annotated[Principal, Depends(require_scopes("recipes:read"))]
WritePrincipal = Annotated[Principal, Depends(require_scopes("recipes:write"))]


@router.post("", response_model=RecipeView, status_code=status.HTTP_201_CREATED)
async def create_recipe(
    payload: RecipeCreate,
    session: SessionDependency,
    principal: WritePrincipal,
) -> RecipeView:
    return await recipe_service.create_recipe(session, principal.subject, payload)


@router.get("", response_model=RecipePage)
async def list_recipes(
    session: SessionDependency,
    principal: ReadPrincipal,
    query: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    tag: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    max_total_minutes: Annotated[int | None, Query(alias="maxTotalMinutes", ge=0)] = None,
    min_rating: Annotated[int | None, Query(alias="minRating", ge=1, le=5)] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RecipePage:
    return await recipe_service.list_recipes(
        session,
        principal.subject,
        query=query,
        tag=tag,
        max_total_minutes=max_total_minutes,
        min_rating=min_rating,
        cursor=cursor,
        limit=limit,
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
