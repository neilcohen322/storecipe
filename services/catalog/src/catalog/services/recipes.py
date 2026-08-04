"""Recipe workflows: transactions and serialization to view schemas."""

from typing import cast
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalog.models import (
    Ingredient,
    Instruction,
    Recipe,
    RecipeCreationIdempotency,
    RecipeTag,
    Tag,
    User,
)
from catalog.recipe_creation_idempotency import recipe_payload_hash
from catalog.recipe_queries import normalize_query_text
from catalog.schemas import (
    ImportedRecipeCreate,
    IngredientInput,
    RecipeCreate,
    RecipePatch,
    RecipeView,
)
from catalog.services.errors import IdempotencyConflict, RecipeNotFound
from catalog.services.users import advance_catalog_version, resolve_user


def _recipe_query() -> Select[tuple[Recipe]]:
    return select(Recipe).options(
        selectinload(Recipe.ingredients),
        selectinload(Recipe.instructions),
        selectinload(Recipe.recipe_tags).selectinload(RecipeTag.tag),
        selectinload(Recipe.ratings),
    )


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized_tags = (normalize_query_text(tag) for tag in tags)
    return sorted({tag for tag in normalized_tags if tag})


def _build_ingredients(ingredients: list[IngredientInput]) -> list[Ingredient]:
    return [
        Ingredient(
            position=position,
            normalized_name=normalize_query_text(ingredient.name),
            **ingredient.model_dump(),
        )
        for position, ingredient in enumerate(ingredients)
    ]


async def _build_recipe_tags(session: AsyncSession, tags: list[str]) -> list[RecipeTag]:
    names = _normalize_tags(tags)
    existing = list(await session.scalars(select(Tag).where(Tag.name.in_(names)))) if names else []
    tags_by_name = {tag.name: tag for tag in existing}
    for name in names:
        if name not in tags_by_name:
            tags_by_name[name] = Tag(name=name)
            session.add(tags_by_name[name])
    return [RecipeTag(tag=tags_by_name[name]) for name in names]


async def _new_recipe(
    session: AsyncSession,
    user: User,
    payload: RecipeCreate,
    *,
    import_job_id: UUID | None = None,
    source_fingerprint: str | None = None,
) -> Recipe:
    return Recipe(
        user_id=user.id,
        import_job_id=import_job_id,
        title=payload.title,
        source_url=str(payload.source_url) if payload.source_url is not None else None,
        source_fingerprint=source_fingerprint,
        servings=payload.servings,
        prep_minutes=payload.prep_minutes,
        cook_minutes=payload.cook_minutes,
        total_minutes=payload.total_minutes,
        ingredients=_build_ingredients(payload.ingredients),
        instructions=[
            Instruction(position=position, text=text)
            for position, text in enumerate(payload.instructions)
        ],
        recipe_tags=await _build_recipe_tags(session, payload.tags),
    )


def _recipe_view(recipe: Recipe, user_id: UUID) -> RecipeView:
    rating = next((item.value for item in recipe.ratings if item.user_id == user_id), None)
    return RecipeView(
        id=recipe.id,
        title=recipe.title,
        source_url=recipe.source_url,
        servings=recipe.servings,
        prep_minutes=recipe.prep_minutes,
        cook_minutes=recipe.cook_minutes,
        total_minutes=recipe.total_minutes,
        ingredients=[
            {
                "raw_text": ingredient.raw_text,
                "name": ingredient.name,
                "quantity": (
                    float(ingredient.quantity) if ingredient.quantity is not None else None
                ),
                "unit": ingredient.unit,
            }
            for ingredient in recipe.ingredients
        ],
        instructions=[instruction.text for instruction in recipe.instructions],
        tags=sorted(recipe_tag.tag.name for recipe_tag in recipe.recipe_tags),
        rating=rating,
    )


async def get_owned_recipe(session: AsyncSession, user_id: UUID, recipe_id: UUID) -> Recipe:
    """Load a recipe owned by ``user_id`` or raise :class:`RecipeNotFound`."""
    statement = _recipe_query().where(Recipe.id == recipe_id, Recipe.user_id == user_id)
    recipe = await session.scalar(statement)
    if recipe is None:
        raise RecipeNotFound(recipe_id)
    return recipe


async def find_owned_recipe_id_by_source(
    session: AsyncSession, owner_subject: str, source_fingerprint: str
) -> UUID | None:
    return cast(
        UUID | None,
        await session.scalar(
            select(Recipe.id)
            .join(User, User.id == Recipe.user_id)
            .where(
                User.auth_subject == owner_subject,
                Recipe.source_fingerprint == source_fingerprint,
            )
            .limit(1)
        ),
    )


async def _reload_recipe(session: AsyncSession, user_id: UUID, recipe_id: UUID) -> Recipe:
    statement = (
        _recipe_query()
        .where(Recipe.id == recipe_id, Recipe.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    recipe = await session.scalar(statement)
    if recipe is None:
        raise RecipeNotFound(recipe_id)
    return recipe


async def _finalize_new_recipe(session: AsyncSession, user: User, recipe: Recipe) -> RecipeView:
    await session.flush()
    await advance_catalog_version(session, user.id)
    await session.commit()
    loaded = await _reload_recipe(session, user.id, recipe.id)
    return _recipe_view(loaded, user.id)


async def create_recipe(session: AsyncSession, subject: str, payload: RecipeCreate) -> RecipeView:
    user = await resolve_user(session, subject)
    recipe = await _new_recipe(session, user, payload)
    session.add(recipe)
    return await _finalize_new_recipe(session, user, recipe)


async def create_recipe_idempotently(
    session: AsyncSession,
    subject: str,
    idempotency_key: str,
    payload: RecipeCreate,
) -> tuple[RecipeView, bool]:
    user = await resolve_user(session, subject)
    user_id = user.id
    payload_hash = recipe_payload_hash(payload)
    existing = await session.get(
        RecipeCreationIdempotency,
        (user_id, idempotency_key),
    )
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise IdempotencyConflict()
        recipe = await get_owned_recipe(session, user_id, existing.recipe_id)
        return _recipe_view(recipe, user_id), True

    recipe = await _new_recipe(session, user, payload)
    session.add(recipe)
    try:
        await session.flush()
        session.add(
            RecipeCreationIdempotency(
                user_id=user_id,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                recipe_id=recipe.id,
            )
        )
        return await _finalize_new_recipe(session, user, recipe), False
    except IntegrityError:
        await session.rollback()
        winner = await session.get(
            RecipeCreationIdempotency,
            (user_id, idempotency_key),
        )
        if winner is None:
            raise
        if winner.payload_hash != payload_hash:
            raise IdempotencyConflict() from None
        winning_recipe = await get_owned_recipe(session, user_id, winner.recipe_id)
        return _recipe_view(winning_recipe, user_id), True


async def get_recipe(session: AsyncSession, subject: str, recipe_id: UUID) -> RecipeView:
    user = await resolve_user(session, subject)
    recipe = await get_owned_recipe(session, user.id, recipe_id)
    return _recipe_view(recipe, user.id)


async def update_recipe(
    session: AsyncSession, subject: str, recipe_id: UUID, payload: RecipePatch
) -> RecipeView:
    user = await resolve_user(session, subject)
    recipe = await get_owned_recipe(session, user.id, recipe_id)

    scalar_fields = {
        "title",
        "source_url",
        "servings",
        "prep_minutes",
        "cook_minutes",
        "total_minutes",
    }
    for field in scalar_fields & payload.model_fields_set:
        value = getattr(payload, field)
        if field == "source_url" and value is not None:
            value = str(value)
        setattr(recipe, field, value)

    if "ingredients" in payload.model_fields_set and payload.ingredients is not None:
        recipe.ingredients.clear()
        await session.flush()
        recipe.ingredients = _build_ingredients(payload.ingredients)
    if "instructions" in payload.model_fields_set and payload.instructions is not None:
        recipe.instructions = [
            Instruction(position=position, text=text)
            for position, text in enumerate(payload.instructions)
        ]
    if "tags" in payload.model_fields_set and payload.tags is not None:
        recipe.recipe_tags = await _build_recipe_tags(session, payload.tags)

    await advance_catalog_version(session, user.id)
    await session.commit()
    loaded = await _reload_recipe(session, user.id, recipe.id)
    return _recipe_view(loaded, user.id)


async def delete_recipe(session: AsyncSession, subject: str, recipe_id: UUID) -> None:
    user = await resolve_user(session, subject)
    recipe = await get_owned_recipe(session, user.id, recipe_id)
    await session.delete(recipe)
    await advance_catalog_version(session, user.id)
    await session.commit()


async def create_imported_recipe(
    session: AsyncSession, payload: ImportedRecipeCreate
) -> tuple[RecipeView, bool]:
    """Idempotently create the recipe produced by one ingestion job.

    Returns the view and whether the recipe already existed (so the caller can
    respond ``200`` on a replay instead of ``201``).
    """
    user = await resolve_user(session, payload.owner_subject)
    existing = await session.scalar(
        _recipe_query().where(
            Recipe.user_id == user.id,
            Recipe.import_job_id == payload.import_job_id,
        )
    )
    if existing is not None:
        return _recipe_view(existing, user.id), True

    recipe = await _new_recipe(
        session,
        user,
        payload,
        import_job_id=payload.import_job_id,
        source_fingerprint=payload.source_fingerprint,
    )
    session.add(recipe)
    try:
        await advance_catalog_version(session, user.id)
        await session.commit()
    except IntegrityError:
        # A concurrent replay may win the unique (user_id, import_job_id)
        # constraint after our initial lookup. Return that first result.
        await session.rollback()
        winner = await session.scalar(
            _recipe_query().where(
                Recipe.user_id == user.id,
                Recipe.import_job_id == payload.import_job_id,
            )
        )
        if winner is None:
            raise
        return _recipe_view(winner, user.id), True

    loaded = await _reload_recipe(session, user.id, recipe.id)
    return _recipe_view(loaded, user.id), False
