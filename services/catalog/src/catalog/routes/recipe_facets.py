from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict

from catalog.auth import Principal, require_scopes
from catalog.database import SessionDependency
from catalog.recipe_facets import (
    RecipeFacetBrowseRequest,
    RecipeFacetPage,
    RecipeFacetSelectionsRequest,
    RecipeFacetSelectionsResponse,
)
from catalog.services import recipe_facets as recipe_facet_service

router = APIRouter(prefix="/v1", tags=["recipes"])

ReadPrincipal = Annotated[Principal, Depends(require_scopes("recipes:read"))]


class RecipeFacetBrowseParameters(RecipeFacetBrowseRequest):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


@router.get("/recipe-facets", response_model=RecipeFacetPage)
async def list_recipe_facets(
    session: SessionDependency,
    principal: ReadPrincipal,
    parameters: Annotated[RecipeFacetBrowseParameters, Query()],
) -> RecipeFacetPage:
    return await recipe_facet_service.browse_recipe_facets(session, principal.subject, parameters)


@router.post("/recipe-facet-selections", response_model=RecipeFacetSelectionsResponse)
async def resolve_recipe_facet_selections(
    payload: RecipeFacetSelectionsRequest,
    session: SessionDependency,
    principal: ReadPrincipal,
) -> RecipeFacetSelectionsResponse:
    return await recipe_facet_service.resolve_recipe_facet_selections(
        session, principal.subject, payload
    )
