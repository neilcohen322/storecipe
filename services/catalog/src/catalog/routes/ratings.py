from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from catalog.auth import Principal, require_scopes
from catalog.database import SessionDependency
from catalog.schemas import RatingInput, RatingView
from catalog.services import ratings as rating_service

router = APIRouter(prefix="/v1/recipes", tags=["ratings"])

RatingPrincipal = Annotated[Principal, Depends(require_scopes("ratings:write"))]


@router.put("/{recipe_id}/rating", response_model=RatingView)
async def put_rating(
    recipe_id: UUID,
    payload: RatingInput,
    session: SessionDependency,
    principal: RatingPrincipal,
) -> RatingView:
    return await rating_service.put_rating(session, principal.subject, recipe_id, payload.value)


@router.delete("/{recipe_id}/rating", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rating(
    recipe_id: UUID,
    session: SessionDependency,
    principal: RatingPrincipal,
) -> Response:
    await rating_service.delete_rating(session, principal.subject, recipe_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
