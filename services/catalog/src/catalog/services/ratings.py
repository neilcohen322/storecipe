"""Rating workflows: upsert and delete with concurrency recovery."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.models import Rating, User
from catalog.schemas import RatingView
from catalog.services.recipes import get_owned_recipe
from catalog.services.users import advance_catalog_version, resolve_user


async def put_rating(
    session: AsyncSession, subject: str, recipe_id: UUID, value: int
) -> RatingView:
    user = await resolve_user(session, subject)
    await get_owned_recipe(session, user.id, recipe_id)
    user_id = user.id
    rating = await session.get(Rating, (user_id, recipe_id))
    if rating is None:
        rating = Rating(user_id=user_id, recipe_id=recipe_id, value=value)
        session.add(rating)
    else:
        rating.value = value

    try:
        await advance_catalog_version(session, user_id)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        rating = await session.get(Rating, (user_id, recipe_id))
        reloaded_user = await session.get(User, user_id)
        if rating is None or reloaded_user is None:
            raise
        rating.value = value
        await advance_catalog_version(session, user_id)
        await session.commit()
    return RatingView(value=rating.value)


async def delete_rating(session: AsyncSession, subject: str, recipe_id: UUID) -> None:
    user = await resolve_user(session, subject)
    await get_owned_recipe(session, user.id, recipe_id)
    rating = await session.scalar(
        select(Rating).where(Rating.user_id == user.id, Rating.recipe_id == recipe_id)
    )
    if rating is not None:
        await session.delete(rating)
        await advance_catalog_version(session, user.id)
        await session.commit()
