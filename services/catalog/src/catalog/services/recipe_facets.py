from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.models import User
from catalog.recipe_facets import (
    RECIPE_FACET_RATING,
    RECIPE_FACET_RATING_STATES,
    RECIPE_FACET_SORT,
    FacetKind,
    RecipeFacetBounds,
    RecipeFacetBrowseRequest,
    RecipeFacetCursor,
    RecipeFacetPage,
    RecipeFacetSelectionItem,
    RecipeFacetSelectionsRequest,
    RecipeFacetSelectionsResponse,
    encode_facet_cursor,
    facet_search_hash,
    validate_facet_cursor,
)
from catalog.recipe_queries import normalize_query_text
from catalog.repositories.recipe_facets import (
    fetch_distinct_facet_names,
    fetch_observed_names,
    fetch_total_minutes_bounds,
)
from catalog.services.errors import UnstableCatalogSnapshot
from catalog.services.users import resolve_user

_SNAPSHOT_ATTEMPTS = 3


async def _catalog_version(session: AsyncSession, user_id: UUID) -> int:
    version = await session.scalar(select(User.catalog_version).where(User.id == user_id))
    if version is None:
        raise RuntimeError("Cannot read catalog version for a missing user")
    return int(version)


async def _read_stable_catalog_snapshot[T](
    session: AsyncSession,
    user_id: UUID,
    read: Callable[[int], Awaitable[T]],
) -> T:
    for _ in range(_SNAPSHOT_ATTEMPTS):
        version = await _catalog_version(session, user_id)
        result = await read(version)
        if await _catalog_version(session, user_id) == version:
            return result
    raise UnstableCatalogSnapshot()


def _next_cursor(
    *,
    kind: FacetKind,
    user_id: UUID,
    catalog_version: int,
    search: str,
    names: list[str],
    has_more: bool,
) -> str | None:
    if not has_more or not names:
        return None
    return encode_facet_cursor(
        RecipeFacetCursor(
            kind=kind,
            user_id=user_id,
            catalog_version=catalog_version,
            search_hash=facet_search_hash(search),
            last_value=names[-1],
        )
    )


async def _facet_page(
    session: AsyncSession,
    *,
    kind: FacetKind,
    user_id: UUID,
    catalog_version: int,
    search: str,
    cursor: str | None,
    limit: int,
) -> tuple[list[str], str | None]:
    after = None
    if cursor is not None:
        after = validate_facet_cursor(
            cursor,
            kind=kind,
            user_id=user_id,
            catalog_version=catalog_version,
            search=search,
        ).last_value
    names, has_more = await fetch_distinct_facet_names(
        session,
        user_id,
        kind=kind,
        search=search,
        after=after,
        limit=limit,
    )
    return names, _next_cursor(
        kind=kind,
        user_id=user_id,
        catalog_version=catalog_version,
        search=search,
        names=names,
        has_more=has_more,
    )


async def browse_recipe_facets(
    session: AsyncSession,
    subject: str,
    request: RecipeFacetBrowseRequest,
) -> RecipeFacetPage:
    user = await resolve_user(session, subject)
    ingredient_search = request.ingredient_q or ""
    tag_search = request.tag_q or ""

    async def read(version: int) -> RecipeFacetPage:
        ingredients, ingredient_next_cursor = await _facet_page(
            session,
            kind=FacetKind.INGREDIENT,
            user_id=user.id,
            catalog_version=version,
            search=ingredient_search,
            cursor=request.ingredient_cursor,
            limit=request.ingredient_limit,
        )
        tags, tag_next_cursor = await _facet_page(
            session,
            kind=FacetKind.TAG,
            user_id=user.id,
            catalog_version=version,
            search=tag_search,
            cursor=request.tag_cursor,
            limit=request.tag_limit,
        )
        bounds = await fetch_total_minutes_bounds(session, user.id)
        return RecipeFacetPage(
            ingredients=ingredients,
            ingredient_next_cursor=ingredient_next_cursor,
            tags=tags,
            tag_next_cursor=tag_next_cursor,
            total_minutes=(
                None if bounds is None else RecipeFacetBounds(min=bounds[0], max=bounds[1])
            ),
            rating=RECIPE_FACET_RATING,
            rating_state=list(RECIPE_FACET_RATING_STATES),
            sort=RECIPE_FACET_SORT,
        )

    return await _read_stable_catalog_snapshot(session, user.id, read)


async def _resolve_kind(
    session: AsyncSession,
    *,
    user_id: UUID,
    kind: FacetKind,
    requested: list[str],
) -> list[RecipeFacetSelectionItem]:
    normalized_names = [normalize_query_text(name) for name in requested]
    observed = await fetch_observed_names(session, user_id, kind=kind, names=normalized_names)
    return [
        RecipeFacetSelectionItem(
            requested_name=name,
            normalized_name=normalized,
            observed=normalized in observed,
        )
        for name, normalized in zip(requested, normalized_names, strict=True)
    ]


async def resolve_recipe_facet_selections(
    session: AsyncSession,
    subject: str,
    request: RecipeFacetSelectionsRequest,
) -> RecipeFacetSelectionsResponse:
    user = await resolve_user(session, subject)

    async def read(_version: int) -> RecipeFacetSelectionsResponse:
        return RecipeFacetSelectionsResponse(
            ingredients=await _resolve_kind(
                session,
                user_id=user.id,
                kind=FacetKind.INGREDIENT,
                requested=request.ingredients,
            ),
            tags=await _resolve_kind(
                session,
                user_id=user.id,
                kind=FacetKind.TAG,
                requested=request.tags,
            ),
        )

    return await _read_stable_catalog_snapshot(session, user.id, read)
