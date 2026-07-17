from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.auth import Principal, require_scopes
from catalog.database import get_session
from catalog.models import Rating, User
from catalog.recipes import _owned_recipe, _resolve_user
from catalog.schemas import RatingInput, RatingView

router = APIRouter(prefix="/v1/recipes", tags=["ratings"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
RatingPrincipal = Annotated[Principal, Depends(require_scopes("ratings:write"))]


@router.put("/{recipe_id}/rating", response_model=RatingView)
async def put_rating(
    recipe_id: UUID,
    payload: RatingInput,
    session: SessionDependency,
    principal: RatingPrincipal,
) -> RatingView:
    user = await _resolve_user(session, principal.subject)
    await _owned_recipe(session, user.id, recipe_id)
    user_id = user.id
    rating = await session.get(Rating, (user_id, recipe_id))
    if rating is None:
        rating = Rating(user_id=user_id, recipe_id=recipe_id, value=payload.value)
        session.add(rating)
    else:
        rating.value = payload.value

    user.catalog_version += 1
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        rating = await session.get(Rating, (user_id, recipe_id))
        reloaded_user = await session.get(User, user_id)
        if rating is None or reloaded_user is None:
            raise
        rating.value = payload.value
        reloaded_user.catalog_version += 1
        await session.commit()
    return RatingView(value=rating.value)


@router.delete("/{recipe_id}/rating", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rating(
    recipe_id: UUID,
    session: SessionDependency,
    principal: RatingPrincipal,
) -> Response:
    user = await _resolve_user(session, principal.subject)
    await _owned_recipe(session, user.id, recipe_id)
    rating = await session.scalar(
        select(Rating).where(Rating.user_id == user.id, Rating.recipe_id == recipe_id)
    )
    if rating is not None:
        await session.delete(rating)
        user.catalog_version += 1
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
