from uuid import UUID

import pytest
from pydantic import ValidationError

from catalog.recipe_queries import (
    ParsedSort,
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
        ingredients=[" Lime ", "CHICKEN", "lime"],
        tags=["Quick", "quick", "Dinner"],
        sort=["rating:desc", "totalMinutes:asc"],
    )

    assert query.text == "spicy dinner"
    assert query.ingredients == ["chicken", "lime"]
    assert query.tags == ["dinner", "quick"]
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


@pytest.mark.parametrize("token", ["ingredientCoverage:desc", "tagCoverage:asc"])
def test_sort_token_rejects_removed_coverage_fields(token: str) -> None:
    with pytest.raises(ValueError, match=r"sort must use <field>:<asc\|desc>"):
        parse_sort_token(token)


def test_request_rejects_internal_recipe_id_sort() -> None:
    with pytest.raises(ValidationError, match="recipeId.*caller-selectable"):
        RecipeQueryRequest(sort=["recipeId:asc"])


def test_sort_order_changes_query_identity() -> None:
    first = RecipeQueryRequest(sort=["rating:desc", "totalMinutes:asc"])
    second = RecipeQueryRequest(sort=["totalMinutes:asc", "rating:desc"])
    assert recipe_query_hash(first) != recipe_query_hash(second)


def test_normalized_ingredient_and_tag_order_does_not_change_identity() -> None:
    first = RecipeQueryRequest(ingredients=["lime", "chicken"], tags=["quick", "dinner"])
    second = RecipeQueryRequest(ingredients=["chicken", "lime"], tags=["dinner", "quick"])
    assert recipe_query_hash(first) == recipe_query_hash(second)


def test_different_ingredients_or_tags_change_query_identity() -> None:
    base = RecipeQueryRequest()
    assert recipe_query_hash(base) != recipe_query_hash(RecipeQueryRequest(ingredients=["lime"]))
    assert recipe_query_hash(base) != recipe_query_hash(RecipeQueryRequest(tags=["dinner"]))


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
        (dict(sort=["ingredientCoverage:desc"]), ("sort", "ingredientCoverage")),
        (dict(sort=["tagCoverage:desc"]), ("sort", "tagCoverage")),
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
        ({"ingredients": [str(index) for index in range(33)]}, "ingredients"),
        ({"tags": [str(index) for index in range(17)]}, "tags"),
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


def test_request_accepts_max_ingredient_and_tag_counts() -> None:
    request = RecipeQueryRequest(
        ingredients=[f"ingredient-{index}" for index in range(32)],
        tags=[f"tag-{index}" for index in range(16)],
    )

    assert len(request.ingredients) == 32
    assert len(request.tags) == 16


def test_request_rejects_duplicate_overflow_before_dedupe() -> None:
    with pytest.raises(ValidationError, match="ingredients"):
        RecipeQueryRequest(ingredients=["egg"] * 33)
    with pytest.raises(ValidationError, match="tags"):
        RecipeQueryRequest(tags=["quick"] * 17)

    accepted = RecipeQueryRequest(ingredients=["egg"] * 32, tags=["quick"] * 16)
    assert accepted.ingredients == ["egg"]
    assert accepted.tags == ["quick"]


def test_request_rejects_empty_normalized_list_items() -> None:
    with pytest.raises(ValidationError, match="ingredients"):
        RecipeQueryRequest(ingredients=[" \t "])
    with pytest.raises(ValidationError, match="tags"):
        RecipeQueryRequest(tags=[" \t "])


def test_empty_query_request_is_valid() -> None:
    request = RecipeQueryRequest()

    assert request.text is None
    assert request.ingredients == []
    assert request.tags == []
    assert request.sort == []
    assert request.limit == 20


def test_canonical_query_json_is_complete_compact_and_ordered() -> None:
    request = RecipeQueryRequest(
        max_total_minutes=30,
        sort=["rating:desc", "totalMinutes:asc"],
        cursor="opaque",
    )

    assert canonical_query_json(request) == (
        b'{"cursor":"opaque","ingredients":[],"limit":20,'
        b'"max_total_minutes":30,"min_rating":null,'
        b'"rating_state":"any","sort":["rating:desc","totalMinutes:asc"],'
        b'"tags":[],"text":null}'
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


def test_query_page_items_are_direct_recipe_views() -> None:
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
    page = RecipeQueryPage(items=[recipe], next_cursor="next")

    assert page.items[0] == recipe
    assert page.model_dump(mode="json")["items"][0]["title"] == "Soup"
    assert "match" not in page.model_dump(mode="json")["items"][0]
