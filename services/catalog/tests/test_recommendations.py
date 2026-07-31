from math import inf, nan
from uuid import UUID

import pytest
from pydantic import ValidationError

from catalog.recommendations import (
    RecommendationItem,
    RecommendationRequest,
    RecommendationResponse,
    RecommendationScoreComponents,
    canonical_request_json,
    recommendation_request_hash,
)


def test_equivalent_recommendation_requests_have_one_hash() -> None:
    first = RecommendationRequest.model_validate(
        {
            "query": "  WINE   for Winter ",
            "mustIncludeIngredients": [" Basil ", "CHEESE", "basil"],
            "availableIngredients": ["Tomato", " tomato "],
            "requiredTags": ["Dinner", "dinner"],
            "maxTotalMinutes": 45,
        }
    )
    second = RecommendationRequest.model_validate(
        {
            "query": "wine for winter",
            "mustIncludeIngredients": ["cheese", "basil"],
            "availableIngredients": ["tomato"],
            "requiredTags": ["dinner"],
            "maxTotalMinutes": 45,
        }
    )

    assert first == second
    assert first.must_include_ingredients == ["basil", "cheese"]
    assert recommendation_request_hash(first) == recommendation_request_hash(second)


def test_request_normalizes_unicode_whitespace_and_sorts_lists() -> None:
    request = RecommendationRequest.model_validate(
        {
            "query": "  cafe\u0301\tmenu  ",
            "mustIncludeIngredients": ["zucchini", "Cafe\u0301", "café"],
            "availableIngredients": ["  Lemon\n", "apple"],
            "requiredTags": ["Week Night", "DINNER"],
        }
    )

    assert request.query == "café menu"
    assert request.must_include_ingredients == ["café", "zucchini"]
    assert request.available_ingredients == ["apple", "lemon"]
    assert request.required_tags == ["dinner", "week night"]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"query": "x" * 201}, "query"),
        ({"mustIncludeIngredients": ["x" * 201]}, "mustIncludeIngredients"),
        ({"mustIncludeIngredients": [str(index) for index in range(33)]}, "mustIncludeIngredients"),
        ({"availableIngredients": [str(index) for index in range(65)]}, "availableIngredients"),
        ({"requiredTags": [str(index) for index in range(17)]}, "requiredTags"),
        ({"maxTotalMinutes": -1}, "maxTotalMinutes"),
        ({"limit": 0}, "limit"),
        ({"limit": 21}, "limit"),
    ],
)
def test_request_rejects_out_of_bounds_values(payload: dict[str, object], field: str) -> None:
    with pytest.raises(ValidationError) as error:
        RecommendationRequest.model_validate(payload)

    assert field in str(error.value)


def test_request_rejects_empty_normalized_list_items() -> None:
    with pytest.raises(ValidationError, match="mustIncludeIngredients"):
        RecommendationRequest.model_validate({"mustIncludeIngredients": [" \t "]})


def test_empty_request_is_valid_for_rating_based_recommendations() -> None:
    request = RecommendationRequest()

    assert request.query is None
    assert request.must_include_ingredients == []
    assert request.available_ingredients == []
    assert request.required_tags == []
    assert request.limit == 10


def test_empty_query_normalizes_to_null() -> None:
    assert RecommendationRequest(query="  \n\t ").query is None


def test_canonical_request_json_is_stable_complete_and_compact() -> None:
    request = RecommendationRequest(query="Dinner", max_total_minutes=30)

    assert canonical_request_json(request) == (
        b'{"available_ingredients":[],"include_already_rated":false,"limit":10,'
        b'"max_total_minutes":30,"must_include_ingredients":[],"query":"dinner",'
        b'"required_tags":[]}'
    )


def test_response_contract_accepts_deterministic_items() -> None:
    response = RecommendationResponse(
        request=RecommendationRequest(),
        catalog_version=0,
        items=[
            RecommendationItem(
                recipe_id=UUID("00000000-0000-0000-0000-000000000001"),
                score=1.0,
                components=RecommendationScoreComponents(
                    ingredient_coverage=1.0,
                    positive_preference=0.5,
                    time_compatibility=1.0,
                    query_tag_match=0.0,
                    negative_preference_penalty=0.0,
                    previously_rated_penalty=0.0,
                ),
            )
        ],
    )

    assert response.items[0].missing_ingredients == []


@pytest.mark.parametrize("non_finite_score", [nan, inf, -inf])
@pytest.mark.parametrize(
    "component_name",
    [
        "ingredient_coverage",
        "positive_preference",
        "time_compatibility",
        "query_tag_match",
        "negative_preference_penalty",
        "previously_rated_penalty",
    ],
)
def test_score_components_reject_non_finite_values(
    component_name: str, non_finite_score: float
) -> None:
    components = {
        "ingredient_coverage": 0.0,
        "positive_preference": 0.0,
        "time_compatibility": 0.0,
        "query_tag_match": 0.0,
        "negative_preference_penalty": 0.0,
        "previously_rated_penalty": 0.0,
    }
    components[component_name] = non_finite_score

    with pytest.raises(ValidationError, match=component_name):
        RecommendationScoreComponents(**components)


@pytest.mark.parametrize("non_finite_score", [nan, inf, -inf])
def test_recommendation_item_rejects_non_finite_score(non_finite_score: float) -> None:
    with pytest.raises(ValidationError, match="score"):
        RecommendationItem(
            recipe_id=UUID("00000000-0000-0000-0000-000000000001"),
            score=non_finite_score,
            components=RecommendationScoreComponents(
                ingredient_coverage=0.0,
                positive_preference=0.0,
                time_compatibility=0.0,
                query_tag_match=0.0,
                negative_preference_penalty=0.0,
                previously_rated_penalty=0.0,
            ),
        )
