"""Recipe workflows: queries, transactions, and serialization to view schemas."""

import base64
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalog.models import Ingredient, Instruction, Rating, Recipe, RecipeTag, Tag, User
from catalog.schemas import (
    ImportedRecipeCreate,
    RecipeCreate,
    RecipePage,
    RecipePatch,
    RecipeView,
)
from catalog.services.errors import InvalidCursor, InvalidFilter, RecipeNotFound
from catalog.services.users import resolve_user


def _recipe_query() -> Select[tuple[Recipe]]:
    return select(Recipe).options(
        selectinload(Recipe.ingredients),
        selectinload(Recipe.instructions),
        selectinload(Recipe.recipe_tags).selectinload(RecipeTag.tag),
        selectinload(Recipe.ratings),
    )


def _normalize_tags(tags: list[str]) -> list[str]:
    return sorted({" ".join(tag.split()).lower() for tag in tags if tag.strip()})


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _encode_cursor(recipe: Recipe) -> str:
    created_at = recipe.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    raw = f"{created_at.isoformat()}|{recipe.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        timestamp, recipe_id = raw.rsplit("|", 1)
        created_at = datetime.fromisoformat(timestamp)
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp must include a timezone")
        return created_at, UUID(recipe_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursor() from exc


def _nonblank_filter(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InvalidFilter(name)
    return normalized


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
) -> Recipe:
    return Recipe(
        user_id=user.id,
        import_job_id=import_job_id,
        title=payload.title,
        source_url=str(payload.source_url) if payload.source_url is not None else None,
        servings=payload.servings,
        prep_minutes=payload.prep_minutes,
        cook_minutes=payload.cook_minutes,
        total_minutes=payload.total_minutes,
        ingredients=[
            Ingredient(position=position, **ingredient.model_dump())
            for position, ingredient in enumerate(payload.ingredients)
        ],
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


async def create_recipe(session: AsyncSession, subject: str, payload: RecipeCreate) -> RecipeView:
    user = await resolve_user(session, subject)
    recipe = await _new_recipe(session, user, payload)
    user.catalog_version += 1
    session.add(recipe)
    await session.commit()
    loaded = await _reload_recipe(session, user.id, recipe.id)
    return _recipe_view(loaded, user.id)


async def list_recipes(
    session: AsyncSession,
    subject: str,
    *,
    query: str | None = None,
    tag: str | None = None,
    max_total_minutes: int | None = None,
    min_rating: int | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> RecipePage:
    user = await resolve_user(session, subject)
    statement = _recipe_query().where(Recipe.user_id == user.id)
    if query:
        pattern = f"%{_escape_like(_nonblank_filter(query, 'query'))}%"
        statement = statement.where(
            or_(
                Recipe.title.ilike(pattern, escape="\\"),
                Recipe.ingredients.any(
                    or_(
                        Ingredient.name.ilike(pattern, escape="\\"),
                        Ingredient.raw_text.ilike(pattern, escape="\\"),
                    )
                ),
            )
        )
    if tag:
        normalized_tag = " ".join(_nonblank_filter(tag, "tag").split()).lower()
        statement = statement.where(
            Recipe.recipe_tags.any(RecipeTag.tag.has(Tag.name == normalized_tag))
        )
    if max_total_minutes is not None:
        statement = statement.where(
            Recipe.total_minutes.is_not(None),
            Recipe.total_minutes <= max_total_minutes,
        )
    if min_rating is not None:
        statement = statement.where(
            Recipe.ratings.any(and_(Rating.user_id == user.id, Rating.value >= min_rating))
        )
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                Recipe.created_at < cursor_created_at,
                and_(Recipe.created_at == cursor_created_at, Recipe.id < cursor_id),
            )
        )
    statement = statement.order_by(Recipe.created_at.desc(), Recipe.id.desc()).limit(limit + 1)
    recipes = list((await session.scalars(statement)).unique())
    has_more = len(recipes) > limit
    page = recipes[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more and page else None
    return RecipePage(
        items=[_recipe_view(recipe, user.id) for recipe in page],
        next_cursor=next_cursor,
    )


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
        recipe.ingredients = [
            Ingredient(position=position, **ingredient.model_dump())
            for position, ingredient in enumerate(payload.ingredients)
        ]
    if "instructions" in payload.model_fields_set and payload.instructions is not None:
        recipe.instructions = [
            Instruction(position=position, text=text)
            for position, text in enumerate(payload.instructions)
        ]
    if "tags" in payload.model_fields_set and payload.tags is not None:
        recipe.recipe_tags = await _build_recipe_tags(session, payload.tags)

    user.catalog_version += 1
    await session.commit()
    loaded = await _reload_recipe(session, user.id, recipe.id)
    return _recipe_view(loaded, user.id)


async def delete_recipe(session: AsyncSession, subject: str, recipe_id: UUID) -> None:
    user = await resolve_user(session, subject)
    recipe = await get_owned_recipe(session, user.id, recipe_id)
    await session.delete(recipe)
    user.catalog_version += 1
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
    )
    user.catalog_version += 1
    session.add(recipe)
    try:
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
