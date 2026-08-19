"""Short row locks and referenced-key queries for recipe covers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalog.models import Recipe, RecipeImage
from catalog.services.errors import RecipeNotFound


async def lock_owned_recipe(session: AsyncSession, user_id: UUID, recipe_id: UUID) -> Recipe:
    statement = (
        select(Recipe)
        .where(Recipe.id == recipe_id, Recipe.user_id == user_id)
        .options(selectinload(Recipe.cover_image))
        .with_for_update()
    )
    recipe = await session.scalar(statement)
    if recipe is None:
        raise RecipeNotFound(recipe_id)
    return recipe


async def list_referenced_objects(session: AsyncSession) -> list[tuple[str, str]]:
    statement = select(RecipeImage.object_key, RecipeImage.object_generation)
    rows = (await session.execute(statement)).all()
    return [(str(key), str(generation)) for key, generation in rows]
