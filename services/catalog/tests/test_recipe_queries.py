from math import inf, nan
from uuid import UUID

import pytest
from pydantic import ValidationError

from catalog.recipe_queries import (
    ParsedSort,
    RecipeMatch,
    RecipeQueryItem,
    RecipeQueryPage,
    RecipeQueryRequest,
    SortDirection,
    SortField,
    canonical_query_json,
    normalize_query_text,
    parse_sort_token,
    recipe_query_hash,
)
from catalog.schemas import RecipeView


def test_query_normalizes_sets_but_preserves_sort_precedence() -> None:
    query = RecipeQueryRequest(
        text="  SPICY   dinner ",
        required_ingredients=[" Lime ", "CHICKEN", "lime"],
        available_ingredients=["Rice", " rice "],
        preferred_tags=["Quick", "quick", "Dinner"],
        sort=["rating:desc", "totalMinutes:asc"],
    )

    assert query.text == "spicy dinner"
    assert query.required_ingredients == ["chicken", "lime"]
    assert query.available_ingredients == ["rice"]
    assert query.preferred_tags == ["dinner", "quick"]
    assert [(item.field, item.direction) for item in query.parsed_sort] == [
        (SortField.RATING, SortDirection.DESC),
        (SortField.TOTAL_MINUTES, SortDirection.ASC),
    ]


def test_equivalent_query_text_normalizes_unicode_and_whitespace() -> None:
    assert normalize_query_text("  Cafe\u0301\tMENU  ") == "café menu"
    assert normalize_query_text("caf\u00e9\nmenu") == "café menu"


def test_empty_text_normalizes_to_none() -> None:
    assert RecipeQueryRequest(text="  \n\t ").text is None


def test_sort_token_parses_field_and_direction() -> None:
    assert parse_sort_token("totalMinutes:desc") == ParsedSort(
        field=SortField.TOTAL_MINUTES,
        direction=SortDirection.DESC,
    )


@pytest.mark.parametrize("token", ["rating", "unknown:asc", "rating:sideways", "rating:asc:extra"])
def test_sort_token_rejects_invalid_values(token: str) -> None:
    with pytest.raises(ValueError, match=r"sort must use <field>:<asc\|desc>"):
        parse_sort_token(token)


def test_request_rejects_internal_recipe_id_sort() -> None:
    with pytest.raises(ValidationError, match="recipeId.*caller-selectable"):
        RecipeQueryRequest(sort=["recipeId:asc"])


def test_sort_order_changes_query_identity() -> None:
    first = RecipeQueryRequest(sort=["rating:desc", "totalMinutes:asc"])
    second = RecipeQueryRequest(sort=["totalMinutes:asc", "rating:desc"])
    assert recipe_query_hash(first) != recipe_query_hash(second)


@pytest.mark.parametrize(
    ("payload", "field_names"),
    [
        (dict(sort=["rating:asc", "rating:desc"]), ("sort",)),
        (
            dict(
                sort=[
                    "rating:asc",
                    "totalMinutes:asc",
                    "createdAt:asc",
                    "updatedAt:asc",
                    "title:asc",
                    "recipeId:asc",
                    "tagCoverage:asc",
                ]
            ),
            ("sort",),
        ),
        (dict(sort=["ingredientCoverage:desc"]), ("ingredientCoverage", "available_ingredients")),
        (dict(sort=["tagCoverage:desc"]), ("tagCoverage", "preferred_tags")),
        (dict(min_rating=3, rating_state="unrated"), ("min_rating", "rating_state")),
    ],
)
def test_request_rejects_invalid_sort_context(
    payload: dict[str, object], field_names: tuple[str, ...]
) -> None:
    with pytest.raises(ValidationError) as error:
        RecipeQueryRequest(**payload)

    message = str(error.value)
    for field_name in field_names:
        assert field_name in message


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"text": "x" * 201}, "text"),
        ({"required_ingredients": [str(index) for index in range(33)]}, "required_ingredients"),
        ({"available_ingredients": [str(index) for index in range(65)]}, "available_ingredients"),
        ({"required_tags": [str(index) for index in range(17)]}, "required_tags"),
        ({"preferred_tags": [str(index) for index in range(17)]}, "preferred_tags"),
        (
            {
                "sort": [
                    "rating:asc",
                    "totalMinutes:asc",
                    "createdAt:asc",
                    "updatedAt:asc",
                    "title:asc",
                    "recipeId:asc",
                    "tagCoverage:asc",
                ]
            },
            "sort",
        ),
        ({"max_total_minutes": -1}, "max_total_minutes"),
        ({"min_rating": 0}, "min_rating"),
        ({"min_rating": 6}, "min_rating"),
        ({"rating_state": "invalid"}, "rating_state"),
        ({"cursor": "x" * 1025}, "cursor"),
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
    ],
)
def test_request_rejects_documented_bounds(payload: dict[str, object], field: str) -> None:
    with pytest.raises(ValidationError) as error:
        RecipeQueryRequest(**payload)

    assert field in str(error.value)


def test_request_rejects_empty_normalized_list_items() -> None:
    with pytest.raises(ValidationError, match="required_ingredients"):
        RecipeQueryRequest(required_ingredients=[" \t "])


def test_empty_query_request_is_valid() -> None:
    request = RecipeQueryRequest()

    assert request.text is None
    assert request.required_ingredients == []
    assert request.available_ingredients == []
    assert request.required_tags == []
    assert request.preferred_tags == []
    assert request.sort == []
    assert request.limit == 20


def test_canonical_query_json_is_complete_compact_and_ordered() -> None:
    request = RecipeQueryRequest(
        max_total_minutes=30,
        sort=["rating:desc", "totalMinutes:asc"],
        cursor="opaque",
    )

    assert canonical_query_json(request) == (
        b'{"available_ingredients":[],"cursor":"opaque","limit":20,'
        b'"max_total_minutes":30,"min_rating":null,"preferred_tags":[],'
        b'"rating_state":"any","required_ingredients":[],"required_tags":[],'
        b'"sort":["rating:desc","totalMinutes:asc"],"text":null}'
    )

    without_cursor = canonical_query_json(request, exclude_cursor=True)
    assert b'"cursor":null' in without_cursor
    assert b'"cursor":"opaque"' not in without_cursor


def test_canonical_query_hash_is_sha256_of_canonical_json() -> None:
    request = RecipeQueryRequest(sort=["rating:desc"])

    import hashlib

    assert recipe_query_hash(request) == hashlib.sha256(canonical_query_json(request)).hexdigest()
    assert (
        recipe_query_hash(request, exclude_cursor=True)
        == hashlib.sha256(canonical_query_json(request, exclude_cursor=True)).hexdigest()
    )


def test_query_response_contract_contains_recipe_and_match() -> None:
    recipe = RecipeView(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        title="Soup",
        source_url=None,
        servings=2,
        prep_minutes=5,
        cook_minutes=10,
        total_minutes=15,
        ingredients=[],
        instructions=[],
        tags=[],
    )
    page = RecipeQueryPage(
        items=[
            RecipeQueryItem(
                recipe=recipe,
                match=RecipeMatch(
                    ingredient_coverage=1.0,
                    missing_ingredients=[],
                    tag_coverage=0.5,
                    matched_preferred_tags=["quick"],
                    missing_preferred_tags=[],
                ),
            )
        ],
        next_cursor="next",
    )

    assert page.items[0].recipe.title == "Soup"
    assert page.items[0].match is not None


@pytest.mark.parametrize("score", [-0.1, 1.1, nan, inf, -inf])
def test_recipe_match_rejects_non_finite_or_out_of_range_scores(score: float) -> None:
    with pytest.raises(ValidationError, match="ingredient_coverage"):
        RecipeMatch(ingredient_coverage=score)
