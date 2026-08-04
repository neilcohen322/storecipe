from pathlib import Path
from uuid import UUID

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from storecipe_mcp.models import (
    IdempotencyKey,
    RatingView,
    RecipeCreate,
    RecipeCreateIdempotencyKey,
    RecipeQueryPage,
    RecipeQueryRequest,
    RecipeView,
)

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"


def _recipe_payload() -> dict[str, object]:
    return {
        "title": "Tomato soup",
        "sourceUrl": "https://example.com/tomato-soup",
        "servings": 4,
        "prepMinutes": 10,
        "cookMinutes": 20,
        "totalMinutes": 30,
        "ingredients": [
            {"rawText": "2 tomatoes", "name": "tomato", "quantity": 2, "unit": "piece"}
        ],
        "instructions": ["Chop the tomatoes", "Simmer until soft"],
        "tags": ["soup"],
    }


def _recipe_view_payload() -> dict[str, object]:
    return {
        "id": "95da0a55-128e-43c2-bd21-4ef1ec8198fa",
        "title": "Tomato soup",
        "sourceUrl": "https://example.com/tomato-soup",
        "servings": 4,
        "prepMinutes": 10,
        "cookMinutes": 20,
        "totalMinutes": 30,
        "ingredients": [
            {"rawText": "2 tomatoes", "name": "tomato", "quantity": 2, "unit": "piece"}
        ],
        "instructions": ["Chop the tomatoes", "Simmer until soft"],
        "tags": ["soup"],
        "rating": 5,
    }


def test_recipe_create_uses_camel_case_contract_and_openapi_bounds() -> None:
    recipe = RecipeCreate.model_validate(_recipe_payload())

    assert recipe.title == "Tomato soup"
    assert recipe.source_url is not None
    wire_payload = recipe.model_dump(mode="json", by_alias=True)
    assert wire_payload["sourceUrl"] == "https://example.com/tomato-soup"
    assert wire_payload["ingredients"][0]["rawText"] == "2 tomatoes"


def test_recipe_create_source_url_matches_catalog_httpurl_and_openapi_contract() -> None:
    with CONTRACT_PATH.open(encoding="utf-8") as contract_file:
        contract = yaml.safe_load(contract_file)
    source_url_schema = contract["components"]["schemas"]["RecipeCreate"]["properties"]["sourceUrl"]

    assert source_url_schema["type"] == ["string", "null"]
    assert source_url_schema["format"] == "uri"
    assert source_url_schema["minLength"] == 1
    assert source_url_schema["maxLength"] == 2083
    assert source_url_schema["pattern"] == r"^https?://[^/?#]+(?:[/?#].*)?$"
    assert "HTTP(S)" in source_url_schema["description"]
    gateway_source_url_schema = RecipeCreate.model_json_schema(by_alias=True)["properties"][
        "sourceUrl"
    ]
    for key in ("minLength", "maxLength", "pattern", "description"):
        assert gateway_source_url_schema[key] == source_url_schema[key]

    valid_host_only = RecipeCreate.model_validate(
        {**_recipe_payload(), "sourceUrl": "https://example.com"}
    )
    assert str(valid_host_only.source_url) == "https://example.com/"
    assert RecipeCreate.model_validate({**_recipe_payload(), "sourceUrl": None}).source_url is None

    max_length_prefix = "https://example.com/"
    max_length_url = max_length_prefix + "a" * (2083 - len(max_length_prefix))
    assert len(max_length_url) == 2083
    RecipeCreate.model_validate({**_recipe_payload(), "sourceUrl": max_length_url})

    with pytest.raises(ValidationError):
        RecipeCreate.model_validate({**_recipe_payload(), "sourceUrl": max_length_url + "a"})
    for invalid_url in ("ftp://example.com/recipe", "example.com"):
        with pytest.raises(ValidationError):
            RecipeCreate.model_validate({**_recipe_payload(), "sourceUrl": invalid_url})


@pytest.mark.parametrize(
    "payload",
    [
        {**_recipe_payload(), "title": ""},
        {**_recipe_payload(), "title": "x" * 201},
        {**_recipe_payload(), "servings": 0},
        {**_recipe_payload(), "ingredients": []},
        {**_recipe_payload(), "instructions": [""]},
        {**_recipe_payload(), "tags": ["x" * 65]},
    ],
)
def test_recipe_create_rejects_openapi_invalid_values(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RecipeCreate.model_validate(payload)


def test_query_normalizes_set_like_lists_but_preserves_ordered_sorts() -> None:
    query = RecipeQueryRequest.model_validate(
        {
            "requiredIngredient": [" Tomato ", "tomato", "Basil"],
            "availableIngredient": (" onion", "Onion "),
            "requiredTag": {"Weeknight", "weeknight", "Soup"},
            "preferredTag": ["Family", " family "],
            "sort": ["rating:desc", "totalMinutes:asc", "title:desc"],
        }
    )

    assert query.required_ingredients == ["basil", "tomato"]
    assert query.available_ingredients == ["onion"]
    assert query.required_tags == ["soup", "weeknight"]
    assert query.preferred_tags == ["family"]
    assert query.sort == ["rating:desc", "totalMinutes:asc", "title:desc"]
    assert query.model_dump(by_alias=True)["requiredIngredient"] == ["basil", "tomato"]


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("limit", 0),
        ("limit", 101),
        ("minRating", 0),
        ("minRating", 6),
        ("maxTotalMinutes", -1),
        ("sort", ["rating:sideways"]),
        ("requiredIngredient", ["x" * 201]),
        ("requiredTag", ["x" * 65]),
    ],
)
def test_recipe_query_rejects_openapi_invalid_values(field_name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RecipeQueryRequest.model_validate({field_name: value})


def test_recipe_view_and_query_page_match_camel_case_response_contract() -> None:
    payload = _recipe_view_payload()
    payload.update(
        {
            "sourceUrl": None,
            "servings": None,
            "prepMinutes": None,
            "cookMinutes": None,
            "totalMinutes": None,
            "rating": None,
        }
    )
    recipe = RecipeView.model_validate(payload)
    page = RecipeQueryPage.model_validate(
        {"items": [{"recipe": recipe, "match": None}], "nextCursor": None}
    )

    assert isinstance(page.items[0].recipe.id, UUID)
    assert page.model_dump(by_alias=True)["nextCursor"] is None


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("title", ""),
        ("title", "x" * 201),
        ("sourceUrl", "ftp://example.com/recipe"),
        ("servings", 0),
        ("prepMinutes", -1),
        ("ingredients", [{"rawText": "", "name": "tomato"}]),
        ("ingredients", [{"rawText": "tomato", "name": "x" * 201}]),
        ("instructions", [""]),
        ("rating", 6),
    ],
)
def test_recipe_view_rejects_openapi_invalid_values(field_name: str, value: object) -> None:
    payload = _recipe_view_payload()
    payload[field_name] = value

    with pytest.raises(ValidationError):
        RecipeView.model_validate(payload)


def test_rating_view_enforces_one_to_five() -> None:
    assert RatingView(value=5).value == 5
    with pytest.raises(ValidationError):
        RatingView(value=6)


@pytest.mark.parametrize("value", ["", "a" * 256])
def test_generic_idempotency_key_matches_catalog_header_contract(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(IdempotencyKey).validate_python(value)


def test_generic_idempotency_key_accepts_one_to_255_characters() -> None:
    assert TypeAdapter(IdempotencyKey).validate_python("x") == "x"
    assert TypeAdapter(IdempotencyKey).validate_python("a" * 255) == "a" * 255


@pytest.mark.parametrize("value", ["short", "a" * 129, "contains space", "contains/slash"])
def test_recipe_create_idempotency_key_enforces_strict_catalog_create_contract(
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(RecipeCreateIdempotencyKey).validate_python(value)


def test_recipe_create_idempotency_key_accepts_uuid_like_values() -> None:
    value = "550e8400-e29b-41d4-a716-446655440000"

    assert TypeAdapter(RecipeCreateIdempotencyKey).validate_python(value) == value
