from sqlalchemy.ext.asyncio import AsyncSession

from catalog.recipe_queries import (
    RecipeMatch,
    RecipeQueryCursor,
    RecipeQueryItem,
    RecipeQueryPage,
    RecipeQueryRequest,
    encode_cursor,
    recipe_query_hash,
    validate_request_cursor,
)
from catalog.recipe_query_cache import RecipeQueryCache
from catalog.recipe_views import to_recipe_view
from catalog.repositories.recipe_queries import (
    QueryCandidate,
    effective_sort,
    fetch_query_candidates,
)
from catalog.services.users import resolve_user


def _match(request: RecipeQueryRequest, candidate: QueryCandidate) -> RecipeMatch | None:
    if not request.available_ingredients and not request.preferred_tags:
        return None

    ingredient_names = {ingredient.normalized_name for ingredient in candidate.recipe.ingredients}
    available_ingredients = set(request.available_ingredients)
    tag_names = {recipe_tag.tag.name for recipe_tag in candidate.recipe.recipe_tags}
    return RecipeMatch(
        ingredient_coverage=(
            float(candidate.ingredient_coverage)
            if candidate.ingredient_coverage is not None
            else None
        ),
        missing_ingredients=sorted(ingredient_names - available_ingredients),
        tag_coverage=(
            float(candidate.tag_coverage) if candidate.tag_coverage is not None else None
        ),
        matched_preferred_tags=sorted(set(request.preferred_tags) & tag_names),
        missing_preferred_tags=sorted(set(request.preferred_tags) - tag_names),
    )


def _next_cursor(
    request: RecipeQueryRequest, catalog_version: int, candidate: QueryCandidate
) -> str:
    sort = effective_sort(request)
    cursor = RecipeQueryCursor(
        schema_version=2,
        query_hash=recipe_query_hash(request, exclude_cursor=True),
        catalog_version=catalog_version,
        sort=[f"{item.field.value}:{item.direction.value}" for item in sort],
        recipe_id=candidate.recipe.id,
    )
    return encode_cursor(cursor)


def build_query_page(
    request: RecipeQueryRequest,
    catalog_version: int,
    candidates: list[QueryCandidate],
) -> RecipeQueryPage:
    bounded_candidates = candidates[: request.limit + 1]
    page_candidates = bounded_candidates[: request.limit]
    has_more = len(bounded_candidates) > request.limit
    return RecipeQueryPage(
        items=[
            RecipeQueryItem(
                recipe=to_recipe_view(candidate.recipe, rating=candidate.rating),
                match=_match(request, candidate),
            )
            for candidate in page_candidates
        ],
        next_cursor=(
            _next_cursor(request, catalog_version, page_candidates[-1])
            if has_more and page_candidates
            else None
        ),
    )


async def query_recipes(
    session: AsyncSession,
    subject: str,
    request: RecipeQueryRequest,
    cache: RecipeQueryCache,
) -> RecipeQueryPage:
    user = await resolve_user(session, subject)
    cursor = validate_request_cursor(request, user.catalog_version)
    cached = await cache.get(user.id, user.catalog_version, request)
    if cached.value is not None:
        return cached.value
    candidates = await fetch_query_candidates(
        session, user.id, request, page_size=request.limit + 1, cursor=cursor
    )
    page = build_query_page(request, user.catalog_version, candidates)
    await cache.set(user.id, user.catalog_version, request, page)
    return page
