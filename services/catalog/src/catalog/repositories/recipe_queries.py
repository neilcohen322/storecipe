from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Numeric, Select, String, and_, false, func, literal, or_, select, type_coerce
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import TypeDecorator

from catalog.models import Ingredient, Rating, Recipe, RecipeTag, Tag
from catalog.recipe_queries import (
    ParsedSort,
    RecipeQueryCursor,
    RecipeQueryRequest,
    SortDirection,
    SortField,
    parse_cursor_sort_token,
)
from catalog.services.errors import InvalidCursor

_COVERAGE_QUANTUM = Decimal("0.00000000000000000001")


class _CoverageDecimal(TypeDecorator[Decimal]):
    """Keep exact twenty-place coverage when SQLite would otherwise coerce to float."""

    impl = String
    cache_ok = True

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None

        raw = str(value)
        with localcontext() as context:
            context.prec = 50
            if "/" in raw:
                numerator, denominator = raw.split("/", 1)
                result = Decimal(numerator) / Decimal(denominator)
            else:
                result = Decimal(raw)
            return result.quantize(_COVERAGE_QUANTUM, rounding=ROUND_HALF_UP)


class _CoverageRatio(FunctionElement[Decimal]):
    type = _CoverageDecimal()
    inherit_cache = True


class _NumericCoverageRatio(FunctionElement[Decimal]):
    type = Numeric(38, 20, asdecimal=True)
    inherit_cache = True


@compiles(_CoverageRatio)
def _compile_coverage_ratio(element: _CoverageRatio, compiler: SQLCompiler, **kwargs: Any) -> str:
    numerator, denominator = list(element.clauses)
    numerator_sql = compiler.process(numerator, **kwargs)
    denominator_sql = compiler.process(denominator, **kwargs)
    return f"CAST({numerator_sql} AS NUMERIC(38, 20)) / NULLIF({denominator_sql}, 0)"


@compiles(_CoverageRatio, "sqlite")
def _compile_sqlite_coverage_ratio(
    element: _CoverageRatio, compiler: SQLCompiler, **kwargs: Any
) -> str:
    numerator, denominator = list(element.clauses)
    numerator_sql = compiler.process(numerator, **kwargs)
    denominator_sql = compiler.process(denominator, **kwargs)
    return (
        f"CASE WHEN {denominator_sql} = 0 THEN NULL ELSE "
        f"CAST({numerator_sql} AS VARCHAR) || '/' || CAST({denominator_sql} AS VARCHAR) END"
    )


@compiles(_NumericCoverageRatio)
def _compile_numeric_coverage_ratio(
    element: _NumericCoverageRatio, compiler: SQLCompiler, **kwargs: Any
) -> str:
    numerator, denominator = list(element.clauses)
    numerator_sql = compiler.process(numerator, **kwargs)
    denominator_sql = compiler.process(denominator, **kwargs)
    return f"CAST({numerator_sql} AS NUMERIC(38, 20)) / NULLIF({denominator_sql}, 0)"


@compiles(_NumericCoverageRatio, "sqlite")
def _compile_sqlite_numeric_coverage_ratio(
    element: _NumericCoverageRatio, compiler: SQLCompiler, **kwargs: Any
) -> str:
    numerator, denominator = list(element.clauses)
    numerator_sql = compiler.process(numerator, **kwargs)
    denominator_sql = compiler.process(denominator, **kwargs)
    return f"CAST({numerator_sql} AS REAL) / NULLIF(CAST({denominator_sql} AS REAL), 0)"


@dataclass(frozen=True)
class QueryCandidate:
    recipe: Recipe
    rating: int | None
    ingredient_coverage: Decimal | None
    tag_coverage: Decimal | None


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
        select(func.count(func.distinct(Ingredient.normalized_name)))
        .where(
            Ingredient.recipe_id == Recipe.id,
            Ingredient.normalized_name.in_(normalized_names),
        )
        .correlate(Recipe)
        .scalar_subquery()
    )


def _ingredient_total_count() -> ColumnElement[Any]:
    return (
        select(func.count(func.distinct(Ingredient.normalized_name)))
        .where(Ingredient.recipe_id == Recipe.id)
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


def _coverage_or_null(
    numerator: ColumnElement[Any], denominator: ColumnElement[Any]
) -> ColumnElement[Any]:
    return _CoverageRatio(numerator, denominator)


def _coverage_columns(
    request: RecipeQueryRequest,
) -> tuple[
    ColumnElement[Any],
    ColumnElement[Any],
    ColumnElement[Any],
    ColumnElement[Any],
]:
    if request.available_ingredients:
        ingredient_numerator = _ingredient_match_count(request.available_ingredients)
        ingredient_denominator = _ingredient_total_count()
        ingredient_coverage = _coverage_or_null(
            ingredient_numerator,
            ingredient_denominator,
        ).label("ingredient_coverage")
        ingredient_coverage_sort = _NumericCoverageRatio(
            ingredient_numerator,
            ingredient_denominator,
        ).label("ingredient_coverage_sort")
    else:
        ingredient_coverage = type_coerce(literal(None), _CoverageDecimal()).label(
            "ingredient_coverage"
        )
        ingredient_coverage_sort = type_coerce(literal(None), Numeric(38, 20)).label(
            "ingredient_coverage_sort"
        )

    if request.preferred_tags:
        tag_numerator = _tag_match_count(request.preferred_tags)
        tag_denominator = literal(len(request.preferred_tags))
        tag_coverage = _coverage_or_null(tag_numerator, tag_denominator).label("tag_coverage")
        tag_coverage_sort = _NumericCoverageRatio(tag_numerator, tag_denominator).label(
            "tag_coverage_sort"
        )
    else:
        tag_coverage = type_coerce(literal(None), _CoverageDecimal()).label("tag_coverage")
        tag_coverage_sort = type_coerce(literal(None), Numeric(38, 20)).label("tag_coverage_sort")

    return ingredient_coverage, tag_coverage, ingredient_coverage_sort, tag_coverage_sort


def build_recipe_query(user_id: UUID, request: RecipeQueryRequest) -> QueryColumns:
    (
        ingredient_coverage,
        tag_coverage,
        ingredient_coverage_sort,
        tag_coverage_sort,
    ) = _coverage_columns(request)

    statement: Select[Any] = (
        select(Recipe, Rating.value, ingredient_coverage, tag_coverage)
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

    if request.required_ingredients:
        statement = statement.where(
            _ingredient_match_count(request.required_ingredients)
            == len(request.required_ingredients)
        )

    if request.required_tags:
        statement = statement.where(
            _tag_match_count(request.required_tags) == len(request.required_tags)
        )

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
        SortField.INGREDIENT_COVERAGE: ingredient_coverage_sort,
        SortField.TAG_COVERAGE: tag_coverage_sort,
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
        QueryCandidate(
            recipe=recipe,
            rating=rating,
            ingredient_coverage=ingredient_coverage,
            tag_coverage=tag_coverage,
        )
        for recipe, rating, ingredient_coverage, tag_coverage in result.unique().all()
    ]
