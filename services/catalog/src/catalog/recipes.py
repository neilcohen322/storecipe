import base64
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Select, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalog.auth import Principal, require_scopes
from catalog.database import get_session
from catalog.models import Ingredient, Instruction, Rating, Recipe, RecipeTag, Tag, User
from catalog.schemas import ImportedRecipeCreate, RecipeCreate, RecipePage, RecipePatch, RecipeView

router = APIRouter(prefix="/v1/recipes", tags=["recipes"])
internal_router = APIRouter(prefix="/internal/recipes", tags=["internal"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
ReadPrincipal = Annotated[Principal, Depends(require_scopes("recipes:read"))]
WritePrincipal = Annotated[Principal, Depends(require_scopes("recipes:write"))]
InternalPrincipal = Annotated[Principal, Depends(require_scopes("recipes:internal:create"))]


def _recipe_query() -> Select[tuple[Recipe]]:
    return select(Recipe).options(
        selectinload(Recipe.ingredients),
        selectinload(Recipe.instructions),
        selectinload(Recipe.recipe_tags).selectinload(RecipeTag.tag),
        selectinload(Recipe.ratings),
    )


async def _resolve_user(session: AsyncSession, subject: str) -> User:
    user = await session.scalar(select(User).where(User.auth_subject == subject))
    if user is not None:
        return user

    user = User(auth_subject=subject)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(User).where(User.auth_subject == subject))
        if existing is None:
            raise
        return existing
    return user


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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid pagination cursor.",
        ) from exc


def _nonblank_filter(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{name} must contain non-whitespace characters.",
        )
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


async def _owned_recipe(session: AsyncSession, user_id: UUID, recipe_id: UUID) -> Recipe:
    statement = _recipe_query().where(Recipe.id == recipe_id, Recipe.user_id == user_id)
    recipe = await session.scalar(statement)
    if recipe is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found.")
    return recipe


async def _reload_recipe(session: AsyncSession, user_id: UUID, recipe_id: UUID) -> Recipe:
    statement = (
        _recipe_query()
        .where(Recipe.id == recipe_id, Recipe.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    recipe = await session.scalar(statement)
    if recipe is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found.")
    return recipe


@router.post("", response_model=RecipeView, status_code=status.HTTP_201_CREATED)
async def create_recipe(
    payload: RecipeCreate,
    session: SessionDependency,
    principal: WritePrincipal,
) -> RecipeView:
    user = await _resolve_user(session, principal.subject)
    recipe = await _new_recipe(session, user, payload)
    user.catalog_version += 1
    session.add(recipe)
    await session.commit()
    loaded = await _reload_recipe(session, user.id, recipe.id)
    return _recipe_view(loaded, user.id)


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
    user = await _resolve_user(session, principal.subject)
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


@router.get("/{recipe_id}", response_model=RecipeView)
async def get_recipe(
    recipe_id: UUID,
    session: SessionDependency,
    principal: ReadPrincipal,
) -> RecipeView:
    user = await _resolve_user(session, principal.subject)
    recipe = await _owned_recipe(session, user.id, recipe_id)
    return _recipe_view(recipe, user.id)


@router.patch("/{recipe_id}", response_model=RecipeView)
async def update_recipe(
    recipe_id: UUID,
    payload: RecipePatch,
    session: SessionDependency,
    principal: WritePrincipal,
) -> RecipeView:
    user = await _resolve_user(session, principal.subject)
    recipe = await _owned_recipe(session, user.id, recipe_id)

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


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(
    recipe_id: UUID,
    session: SessionDependency,
    principal: WritePrincipal,
) -> Response:
    user = await _resolve_user(session, principal.subject)
    recipe = await _owned_recipe(session, user.id, recipe_id)
    await session.delete(recipe)
    user.catalog_version += 1
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@internal_router.post(
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
    user = await _resolve_user(session, payload.owner_subject)
    existing = await session.scalar(
        _recipe_query().where(
            Recipe.user_id == user.id,
            Recipe.import_job_id == payload.import_job_id,
        )
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _recipe_view(existing, user.id)

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
        response.status_code = status.HTTP_200_OK
        return _recipe_view(winner, user.id)

    loaded = await _reload_recipe(session, user.id, recipe.id)
    return _recipe_view(loaded, user.id)
