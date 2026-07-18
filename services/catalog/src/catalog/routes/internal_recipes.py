from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from catalog.auth import Principal, require_scopes
from catalog.database import SessionDependency
from catalog.schemas import ImportedRecipeCreate, RecipeView
from catalog.services import recipes as recipe_service

router = APIRouter(prefix="/internal/recipes", tags=["internal"])

InternalPrincipal = Annotated[Principal, Depends(require_scopes("recipes:internal:create"))]


@router.post(
    "/imported",
    response_model=RecipeView,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_imported_recipe(
    payload: ImportedRecipeCreate,
    response: Response,
    session: SessionDependency,
    _principal: InternalPrincipal,
) -> RecipeView:
    """Idempotently create the recipe produced by one ingestion job."""
    view, already_existed = await recipe_service.create_imported_recipe(session, payload)
    if already_existed:
        response.status_code = status.HTTP_200_OK
    return view
