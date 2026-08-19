from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from catalog.auth import Principal, require_scopes
from catalog.database import SessionDependency
from catalog.mutation_quota import enforce_mutation_quota
from catalog.schemas import RatingInput, RatingView
from catalog.services import ratings as rating_service

router = APIRouter(prefix="/v1/recipes", tags=["ratings"])

RatingPrincipal = Annotated[Principal, Depends(require_scopes("ratings:write"))]


async def rating_mutation_principal(
    request: Request,
    response: Response,
    principal: RatingPrincipal,
) -> Principal:
    await enforce_mutation_quota(request, response, principal.subject)
    return principal


RatingMutationPrincipal = Annotated[Principal, Depends(rating_mutation_principal)]


@router.put("/{recipe_id}/rating", response_model=RatingView)
async def put_rating(
    recipe_id: UUID,
    payload: RatingInput,
    response: Response,
    session: SessionDependency,
    principal: RatingMutationPrincipal,
) -> RatingView:
    return await rating_service.put_rating(session, principal.subject, recipe_id, payload.value)


@router.delete("/{recipe_id}/rating", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rating(
    recipe_id: UUID,
    response: Response,
    session: SessionDependency,
    principal: RatingMutationPrincipal,
) -> Response:
    await rating_service.delete_rating(session, principal.subject, recipe_id)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
