from pathlib import Path
from uuid import UUID

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from storecipe_mcp.models import (
    CatalogRecipeCreate,
    IdempotencyKey,
    IngredientCreate,
    IngredientDraft,
    RatingView,
    RecipeCreate,
    RecipeCreateIdempotencyKey,
    RecipeFacetSelectionItem,
    RecipeFacetSelectionsRequest,
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
        "ingredients": [{"rawText": "2 tomatoes"}],
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
            {
                "rawText": "2 tomatoes",
                "name": "tomato",
                "canonicalName": "tomato",
                "quantity": 2,
                "unit": "piece",
            }
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
    assert "name" not in wire_payload["ingredients"][0]


def test_recipe_create_ingredient_drafts_reject_structured_fields() -> None:
    with pytest.raises(ValidationError):
        RecipeCreate.model_validate(
            {
                **_recipe_payload(),
                "ingredients": [
                    {
                        "rawText": "2 tomatoes",
                        "name": "tomato",
                        "quantity": 2,
                        "unit": "piece",
                    }
                ],
            }
        )


def test_catalog_recipe_create_includes_canonical_name_on_ingredients() -> None:
    payload = CatalogRecipeCreate.model_validate(
        {
            **_recipe_payload(),
            "ingredients": [
                {
                    "rawText": "2 tomatoes",
                    "name": "tomato",
                    "canonicalName": "tomato",
                    "quantity": 2,
                    "unit": "piece",
                }
            ],
        }
    )

    wire_payload = payload.model_dump(mode="json", by_alias=True)
    assert wire_payload["ingredients"][0]["canonicalName"] == "tomato"


def test_ingredient_create_requires_canonical_name() -> None:
    IngredientCreate.model_validate(
        {
            "rawText": "2 tomatoes",
            "name": "tomato",
            "canonicalName": "tomato",
            "quantity": 2,
            "unit": "piece",
        }
    )
    with pytest.raises(ValidationError):
        IngredientCreate.model_validate(
            {"rawText": "2 tomatoes", "name": "tomato", "quantity": 2, "unit": "piece"}
        )


def test_public_recipe_create_ingredients_differ_from_catalog_openapi_ingredient() -> None:
    with CONTRACT_PATH.open(encoding="utf-8") as contract_file:
        contract = yaml.safe_load(contract_file)
    catalog_ingredient_schema = contract["components"]["schemas"]["Ingredient"]
    public_ingredient_schema = IngredientDraft.model_json_schema(by_alias=True)

    assert catalog_ingredient_schema["required"] == ["rawText", "name", "canonicalName"]
    assert public_ingredient_schema["required"] == ["rawText"]
    assert "name" not in public_ingredient_schema["properties"]
    assert "canonicalName" not in public_ingredient_schema["properties"]


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


def test_query_normalizes_ingredient_and_tag_lists_but_preserves_ordered_sorts() -> None:
    query = RecipeQueryRequest.model_validate(
        {
            "ingredient": [" Tomato ", "tomato", "Basil"],
            "tag": {"Weeknight", "weeknight", "Soup"},
            "sort": ["rating:desc", "totalMinutes:asc", "title:desc"],
        }
    )

    assert query.ingredients == ["basil", "tomato"]
    assert query.tags == ["soup", "weeknight"]
    assert query.sort == ["rating:desc", "totalMinutes:asc", "title:desc"]
    dumped = query.model_dump(by_alias=True)
    assert dumped["ingredient"] == ["basil", "tomato"]
    assert dumped["tag"] == ["soup", "weeknight"]
    assert "requiredIngredient" not in dumped
    assert "availableIngredient" not in dumped
    assert "requiredTag" not in dumped
    assert "preferredTag" not in dumped


def test_recipe_query_schema_accepts_only_ingredient_and_tag_lists() -> None:
    schema = RecipeQueryRequest.model_json_schema(by_alias=True)
    properties = schema["properties"]
    assert "ingredient" in properties
    assert "tag" in properties
    assert properties["ingredient"]["maxItems"] == 32
    assert properties["tag"]["maxItems"] == 16
    for removed in (
        "requiredIngredient",
        "availableIngredient",
        "requiredTag",
        "preferredTag",
    ):
        assert removed not in properties
        with pytest.raises(ValidationError):
            RecipeQueryRequest.model_validate({removed: ["tomato"]})
    serialized = str(schema)
    assert "ingredientCoverage" not in serialized
    assert "tagCoverage" not in serialized
    with pytest.raises(ValidationError):
        RecipeQueryRequest.model_validate({"sort": ["ingredientCoverage:desc"]})
    with pytest.raises(ValidationError):
        RecipeQueryRequest.model_validate({"sort": ["tagCoverage:asc"]})


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("limit", 0),
        ("limit", 101),
        ("minRating", 0),
        ("minRating", 6),
        ("maxTotalMinutes", -1),
        ("sort", ["rating:sideways"]),
        ("ingredient", ["x" * 201]),
        ("tag", ["x" * 65]),
    ],
)
def test_recipe_query_rejects_openapi_invalid_values(field_name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RecipeQueryRequest.model_validate({field_name: value})


def test_recipe_query_rejects_duplicate_overflow_before_dedupe() -> None:
    with pytest.raises(ValidationError):
        RecipeQueryRequest.model_validate({"ingredient": ["egg"] * 33})
    with pytest.raises(ValidationError):
        RecipeQueryRequest.model_validate({"tag": ["quick"] * 17})
    accepted = RecipeQueryRequest.model_validate(
        {"ingredient": ["egg"] * 32, "tag": ["quick"] * 16}
    )
    assert accepted.ingredients == ["egg"]
    assert accepted.tags == ["quick"]


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
    page = RecipeQueryPage.model_validate({"items": [recipe], "nextCursor": None})

    assert isinstance(page.items[0].id, UUID)
    assert "match" not in page.model_dump(by_alias=True)["items"][0]
    assert page.model_dump(by_alias=True)["nextCursor"] is None


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("title", ""),
        ("title", "x" * 201),
        ("sourceUrl", "ftp://example.com/recipe"),
        ("servings", 0),
        ("prepMinutes", -1),
        ("ingredients", [{"rawText": "", "name": "tomato", "canonicalName": "tomato"}]),
        ("ingredients", [{"rawText": "tomato", "name": "x" * 201, "canonicalName": "tomato"}]),
        ("ingredients", [{"rawText": "tomato", "name": "tomato", "canonicalName": ""}]),
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


def test_facet_selection_item_preserves_padded_requested_name() -> None:
    item = RecipeFacetSelectionItem.model_validate(
        {
            "requestedName": "  tomato  ",
            "normalizedName": "tomato",
            "observed": True,
        }
    )
    assert item.requested_name == "  tomato  "
    assert item.model_dump(by_alias=True)["requestedName"] == "  tomato  "


def test_facet_selections_request_matches_query_limits() -> None:
    properties = RecipeFacetSelectionsRequest.model_json_schema()["properties"]
    assert properties["ingredients"]["maxItems"] == 32
    assert properties["tags"]["maxItems"] == 16
    RecipeFacetSelectionsRequest.model_validate(
        {
            "ingredients": [str(index) for index in range(32)],
            "tags": [str(index) for index in range(16)],
        }
    )
    with pytest.raises(ValidationError):
        RecipeFacetSelectionsRequest.model_validate(
            {"ingredients": [str(index) for index in range(33)]}
        )
    with pytest.raises(ValidationError):
        RecipeFacetSelectionsRequest.model_validate({"tags": [str(index) for index in range(17)]})
