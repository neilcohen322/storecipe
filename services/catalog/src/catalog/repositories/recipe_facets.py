from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from catalog.models import Ingredient, Recipe, RecipeTag, Tag
from catalog.recipe_facets import FacetKind


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _facet_column(kind: FacetKind) -> ColumnElement[Any]:
    if kind is FacetKind.INGREDIENT:
        return cast(ColumnElement[Any], Ingredient.canonical_name)
    return cast(ColumnElement[Any], Tag.name)


def _owner_facet_statement(user_id: UUID, kind: FacetKind) -> Select[tuple[str]]:
    column = _facet_column(kind)
    if kind is FacetKind.INGREDIENT:
        return select(column).join(Recipe).where(Recipe.user_id == user_id).distinct()
    return select(column).join(RecipeTag).join(Recipe).where(Recipe.user_id == user_id).distinct()


async def fetch_distinct_facet_names(
    session: AsyncSession,
    user_id: UUID,
    *,
    kind: FacetKind,
    search: str,
    after: str | None,
    limit: int,
) -> tuple[list[str], bool]:
    column = _facet_column(kind)
    statement = _owner_facet_statement(user_id, kind)
    if search != "":
        pattern = "%" + _escape_like(search) + "%"
        if kind is FacetKind.INGREDIENT:
            statement = statement.where(
                or_(
                    Ingredient.canonical_name.like(pattern, escape="\\"),
                    Ingredient.normalized_name.like(pattern, escape="\\"),
                )
            )
        else:
            statement = statement.where(column.like(pattern, escape="\\"))
    if after is not None:
        statement = statement.where(column > after)
    statement = statement.order_by(column).limit(limit + 1)
    names = list((await session.execute(statement)).scalars().all())
    return names[:limit], len(names) > limit


async def fetch_total_minutes_bounds(
    session: AsyncSession, user_id: UUID
) -> tuple[int, int] | None:
    statement = select(func.min(Recipe.total_minutes), func.max(Recipe.total_minutes)).where(
        Recipe.user_id == user_id,
        Recipe.total_minutes.is_not(None),
    )
    minimum, maximum = (await session.execute(statement)).one()
    if minimum is None:
        return None
    return int(minimum), int(maximum)


async def fetch_owner_ingredient_identities(
    session: AsyncSession,
    user_id: UUID,
    *,
    names: Sequence[str] | None = None,
) -> list[tuple[str, str]]:
    if names is not None and not names:
        return []
    statement = (
        select(Ingredient.canonical_name, Ingredient.normalized_name)
        .join(Recipe)
        .where(Recipe.user_id == user_id)
        .distinct()
    )
    if names:
        statement = statement.where(
            or_(Ingredient.canonical_name.in_(names), Ingredient.normalized_name.in_(names))
        )
    rows = (await session.execute(statement)).all()
    return [(row[0], row[1]) for row in rows]


async def fetch_observed_names(
    session: AsyncSession,
    user_id: UUID,
    *,
    kind: FacetKind,
    names: Sequence[str],
) -> set[str]:
    if not names:
        return set()
    column = _facet_column(kind)
    statement = _owner_facet_statement(user_id, kind).where(column.in_(names))
    return set((await session.execute(statement)).scalars().all())
