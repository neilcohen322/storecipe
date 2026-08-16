from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from catalog.errors import InvalidCursor
from catalog.models import Ingredient, Rating, Recipe, RecipeTag, Tag
from catalog.recipe_queries import (
    ParsedSort,
    RecipeQueryCursor,
    RecipeQueryRequest,
    SortDirection,
    SortField,
    parse_cursor_sort_token,
)


@dataclass(frozen=True)
class QueryCandidate:
    recipe: Recipe
    rating: int | None


@dataclass(frozen=True)
class QueryColumns:
    statement: Select[Any]
    expressions: Mapping[SortField, ColumnElement[Any]]


def _recipe_load_options() -> tuple[Any, ...]:
    return (
        selectinload(Recipe.ingredients),
        selectinload(Recipe.instructions),
        selectinload(Recipe.recipe_tags).selectinload(RecipeTag.tag),
        selectinload(Recipe.ratings),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ingredient_match_count(
    normalized_names: list[str],
) -> ColumnElement[Any]:
    return (
        select(func.count(func.distinct(Ingredient.canonical_name)))
        .where(
            Ingredient.recipe_id == Recipe.id,
            Ingredient.canonical_name.in_(normalized_names),
        )
        .correlate(Recipe)
        .scalar_subquery()
    )


def _tag_match_count(normalized_names: list[str]) -> ColumnElement[Any]:
    return (
        select(func.count(func.distinct(Tag.name)))
        .select_from(RecipeTag)
        .join(Tag, Tag.id == RecipeTag.tag_id)
        .where(RecipeTag.recipe_id == Recipe.id, Tag.name.in_(normalized_names))
        .correlate(Recipe)
        .scalar_subquery()
    )


def build_recipe_query(user_id: UUID, request: RecipeQueryRequest) -> QueryColumns:
    statement: Select[Any] = (
        select(Recipe, Rating.value)
        .outerjoin(
            Rating,
            and_(Rating.recipe_id == Recipe.id, Rating.user_id == user_id),
        )
        .where(Recipe.user_id == user_id)
    )

    if request.text is not None:
        pattern = f"%{_escape_like(request.text)}%"
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

    if request.ingredients:
        statement = statement.where(
            _ingredient_match_count(request.ingredients) == len(request.ingredients)
        )

    if request.tags:
        statement = statement.where(_tag_match_count(request.tags) == len(request.tags))

    if request.max_total_minutes is not None:
        statement = statement.where(
            Recipe.total_minutes.is_not(None),
            Recipe.total_minutes <= request.max_total_minutes,
        )

    if request.min_rating is not None:
        statement = statement.where(
            Rating.value.is_not(None),
            Rating.value >= request.min_rating,
        )

    if request.rating_state == "rated":
        statement = statement.where(Rating.value.is_not(None))
    elif request.rating_state == "unrated":
        statement = statement.where(Rating.value.is_(None))

    expressions: dict[SortField, ColumnElement[Any]] = {
        SortField.RATING: cast(ColumnElement[Any], Rating.value),
        SortField.TOTAL_MINUTES: cast(ColumnElement[Any], Recipe.total_minutes),
        SortField.CREATED_AT: cast(ColumnElement[Any], Recipe.created_at),
        SortField.UPDATED_AT: cast(ColumnElement[Any], Recipe.updated_at),
        SortField.TITLE: func.lower(Recipe.title),
        SortField.RECIPE_ID: cast(ColumnElement[Any], Recipe.id),
    }
    return QueryColumns(statement=statement, expressions=expressions)


def effective_sort(request: RecipeQueryRequest) -> tuple[ParsedSort, ...]:
    requested_sorts = request.parsed_sort or (ParsedSort(SortField.CREATED_AT, SortDirection.DESC),)
    return (*requested_sorts, ParsedSort(SortField.RECIPE_ID, SortDirection.ASC))


def apply_ordering(query: QueryColumns, sort: tuple[ParsedSort, ...]) -> QueryColumns:
    order_by = []
    for item in sort:
        expression = query.expressions[item.field]
        if item.direction is SortDirection.ASC:
            order_by.append(expression.asc().nulls_last())
        else:
            order_by.append(expression.desc().nulls_last())
    return QueryColumns(
        statement=query.statement.order_by(*order_by), expressions=query.expressions
    )


def _equal_value(expression: ColumnElement[Any], value: object | None) -> ColumnElement[bool]:
    if value is None:
        return expression.is_(None)
    return expression == value


def _after_value(
    expression: ColumnElement[Any],
    value: object | None,
    direction: SortDirection,
) -> ColumnElement[bool] | None:
    if value is None:
        return None
    if direction is SortDirection.ASC:
        return or_(expression > value, expression.is_(None))
    return or_(expression < value, expression.is_(None))


def apply_keyset_cursor(
    query: QueryColumns,
    cursor: RecipeQueryCursor,
    values: Sequence[object | None],
) -> QueryColumns:
    try:
        parsed_sort = tuple(parse_cursor_sort_token(token) for token in cursor.sort)
        if len(parsed_sort) != len(values):
            raise InvalidCursor()

        branches: list[ColumnElement[bool]] = []
        for index, item in enumerate(parsed_sort):
            after = _after_value(
                query.expressions[item.field],
                values[index],
                item.direction,
            )
            if after is None:
                continue
            preceding = [
                _equal_value(query.expressions[previous.field], values[position])
                for position, previous in enumerate(parsed_sort[:index])
            ]
            branches.append(and_(*preceding, after))
    except (KeyError, InvalidCursor, ValueError) as exc:
        if isinstance(exc, InvalidCursor):
            raise
        raise InvalidCursor() from exc

    predicate = or_(*branches) if branches else false()
    return QueryColumns(statement=query.statement.where(predicate), expressions=query.expressions)


async def _fetch_cursor_boundary_values(
    session: AsyncSession,
    query: QueryColumns,
    cursor: RecipeQueryCursor,
) -> tuple[object | None, ...]:
    try:
        parsed_sort = tuple(parse_cursor_sort_token(token) for token in cursor.sort)
        expressions = tuple(query.expressions[item.field] for item in parsed_sort)
    except (KeyError, ValueError) as exc:
        raise InvalidCursor() from exc

    statement = (
        query.statement.with_only_columns(*expressions)
        .where(Recipe.id == cursor.recipe_id)
        .order_by(None)
        .limit(1)
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise InvalidCursor()
    return tuple(row)


async def fetch_query_candidates(
    session: AsyncSession,
    user_id: UUID,
    request: RecipeQueryRequest,
    *,
    page_size: int,
    cursor: RecipeQueryCursor | None = None,
) -> list[QueryCandidate]:
    query = build_recipe_query(user_id, request)
    if cursor is not None:
        boundary_values = await _fetch_cursor_boundary_values(session, query, cursor)
        query = apply_keyset_cursor(query, cursor, boundary_values)
    query = apply_ordering(query, effective_sort(request))
    result = await session.execute(
        query.statement.options(*_recipe_load_options()).limit(page_size)
    )
    return [
        QueryCandidate(recipe=recipe, rating=rating) for recipe, rating in result.unique().all()
    ]
