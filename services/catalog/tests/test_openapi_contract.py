from pathlib import Path

import yaml

CONTRACT_PATH = Path(__file__).parents[3] / "contracts" / "openapi.yaml"


def _contract() -> dict[str, object]:
    with CONTRACT_PATH.open(encoding="utf-8") as contract_file:
        return yaml.safe_load(contract_file)


def test_recipe_query_get_documents_stale_cursor_conflict() -> None:
    contract = _contract()
    operation = contract["paths"]["/v1/recipes"]["get"]

    assert operation["responses"]["409"] == {
        "$ref": "#/components/responses/StaleRecipeQueryCursor"
    }
    response = contract["components"]["responses"]["StaleRecipeQueryCursor"]
    example = response["content"]["application/problem+json"]["example"]
    assert example["type"].endswith("/stale_recipe_query_cursor")
    assert example["errorCategory"] == "stale_recipe_query_cursor"


def test_recipe_create_documents_idempotency_boundary() -> None:
    contract = _contract()
    operation = contract["paths"]["/v1/recipes"]["post"]

    parameters = operation["parameters"]
    assert len(parameters) == 1
    parameter = parameters[0]
    assert parameter == {"$ref": "#/components/parameters/RecipeCreateIdempotencyKey"}

    key_parameter = contract["components"]["parameters"]["RecipeCreateIdempotencyKey"]
    assert key_parameter["name"] == "Idempotency-Key"
    assert key_parameter["in"] == "header"
    assert key_parameter["required"] is True
    assert key_parameter["schema"] == {
        "type": "string",
        "minLength": 8,
        "maxLength": 128,
        "pattern": "^[A-Za-z0-9._:-]+$",
    }

    assert operation["responses"]["200"]["description"] == "Existing idempotent recipe replay"
    assert operation["responses"]["201"]["description"] == "Created recipe"
    assert operation["responses"]["409"] == {"$ref": "#/components/responses/IdempotencyConflict"}
    assert operation["responses"]["422"] == {"$ref": "#/components/responses/ValidationError"}

    conflict = contract["components"]["responses"]["IdempotencyConflict"]
    assert conflict["content"]["application/problem+json"]["schema"] == {
        "$ref": "#/components/schemas/Problem"
    }
    example = conflict["content"]["application/problem+json"]["example"]
    assert example["type"].endswith("/idempotency_conflict")
    assert example["errorCategory"] == "idempotency_conflict"


def test_recipe_source_urls_document_catalog_http_url_behavior() -> None:
    contract = _contract()

    for schema_name in ("RecipeCreate", "RecipePatch", "Recipe"):
        source_url = contract["components"]["schemas"][schema_name]["properties"]["sourceUrl"]
        assert source_url == {
            "type": ["string", "null"],
            "format": "uri",
            "minLength": 1,
            "maxLength": 2083,
            "pattern": "^https?://[^/?#]+(?:[/?#].*)?$",
            "description": (
                "HTTP(S) URL with a host; Catalog normalizes a host-only URL with a trailing slash."
            ),
        }


REMOVED_RECIPE_QUERY_KEYS = (
    "requiredIngredient",
    "availableIngredient",
    "requiredTag",
    "preferredTag",
)
COVERAGE_SORT_TOKENS = (
    "ingredientCoverage:asc",
    "ingredientCoverage:desc",
    "tagCoverage:asc",
    "tagCoverage:desc",
)
RECIPE_SORT_TOKENS = [
    "rating:asc",
    "rating:desc",
    "totalMinutes:asc",
    "totalMinutes:desc",
    "createdAt:asc",
    "createdAt:desc",
    "updatedAt:asc",
    "updatedAt:desc",
    "title:asc",
    "title:desc",
]


def test_recipe_query_get_exposes_and_ingredient_and_tag_lists() -> None:
    contract = _contract()
    operation = contract["paths"]["/v1/recipes"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert "requiredIngredient" not in parameters
    assert "availableIngredient" not in parameters
    assert "requiredTag" not in parameters
    assert "preferredTag" not in parameters

    ingredient = parameters["ingredient"]
    tag = parameters["tag"]
    assert ingredient["in"] == "query"
    assert ingredient["style"] == "form"
    assert ingredient["explode"] is True
    assert ingredient["description"] == "Every listed value is required (AND)."
    assert ingredient["schema"] == {
        "type": "array",
        "maxItems": 32,
        "items": {"type": "string", "minLength": 1, "maxLength": 200},
    }
    assert tag["in"] == "query"
    assert tag["style"] == "form"
    assert tag["explode"] is True
    assert tag["description"] == "Every listed value is required (AND)."
    assert tag["schema"] == {
        "type": "array",
        "maxItems": 16,
        "items": {"type": "string", "minLength": 1, "maxLength": 64},
    }


def test_recipe_query_contract_omits_coverage_ranking_and_match_wrappers() -> None:
    contract = _contract()
    schemas = contract["components"]["schemas"]
    operation = contract["paths"]["/v1/recipes"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert schemas["RecipeQueryPage"]["properties"]["items"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/Recipe"},
    }
    assert "RecipeMatch" not in schemas
    assert "RecipeQueryItem" not in schemas
    assert "RecipeFacetSort" not in schemas
    assert schemas["RecipeSort"]["enum"] == RECIPE_SORT_TOKENS
    assert parameters["sort"]["schema"] == {
        "type": "array",
        "maxItems": 6,
        "items": {"$ref": "#/components/schemas/RecipeSort"},
    }
    assert "coverageFirst" not in parameters["sort"].get("examples", {})
    serialized = yaml.dump(contract)
    for removed in (*REMOVED_RECIPE_QUERY_KEYS, *COVERAGE_SORT_TOKENS):
        assert removed not in serialized


def test_recipe_facet_page_exposes_unconditional_sort_list() -> None:
    contract = _contract()
    schemas = contract["components"]["schemas"]
    assert schemas["RecipeFacetPage"]["properties"]["sort"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/RecipeSort"},
    }


def test_openapi_31_uses_type_unions_instead_of_legacy_nullable() -> None:
    contract = _contract()

    def nullable_nodes(value: object) -> list[dict[str, object]]:
        if isinstance(value, dict):
            matches = [value] if "nullable" in value else []
            return matches + [match for child in value.values() for match in nullable_nodes(child)]
        if isinstance(value, list):
            return [match for child in value for match in nullable_nodes(child)]
        return []

    assert nullable_nodes(contract) == []

    parameters = contract["paths"]["/v1/recipes"]["get"]["parameters"]
    parameter_schemas = {parameter["name"]: parameter["schema"] for parameter in parameters}
    assert parameter_schemas["text"]["type"] == ["string", "null"]
    assert parameter_schemas["maxTotalMinutes"]["type"] == ["integer", "null"]
    assert parameter_schemas["minRating"]["type"] == ["integer", "null"]
    assert parameter_schemas["cursor"]["type"] == ["string", "null"]


def test_recipe_facet_selections_post_documents_resolution_contract() -> None:
    contract = _contract()
    operation = contract["paths"]["/v1/recipe-facet-selections"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in schema:
        schema = contract["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    ingredients = schema["properties"]["ingredients"]
    tags = schema["properties"]["tags"]
    assert schema.get("additionalProperties") is False
    assert ingredients["maxItems"] == 32
    assert ingredients["items"] == {"type": "string", "minLength": 1, "maxLength": 200}
    assert tags["maxItems"] == 16
    assert tags["items"] == {"type": "string", "minLength": 1, "maxLength": 64}
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/RecipeFacetSelectionsResponse"}
    assert operation["responses"]["422"] == {"$ref": "#/components/responses/ValidationError"}
    assert operation["responses"]["503"] == {"$ref": "#/components/responses/ServiceUnavailable"}


def test_recipe_facets_get_documents_browse_contract() -> None:
    contract = _contract()
    operation = contract["paths"]["/v1/recipe-facets"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert set(parameters) == {
        "ingredientLimit",
        "tagLimit",
        "ingredientCursor",
        "tagCursor",
        "ingredientQ",
        "tagQ",
    }
    assert parameters["ingredientCursor"]["schema"]["maxLength"] == 2048
    assert parameters["tagCursor"]["schema"]["maxLength"] == 2048
    assert operation["responses"]["409"] == {
        "$ref": "#/components/responses/StaleRecipeFacetCursor"
    }
    assert operation["responses"]["414"] == {"$ref": "#/components/responses/UriTooLong"}
    assert operation["responses"]["422"] == {"$ref": "#/components/responses/ValidationError"}
    assert operation["responses"]["503"] == {"$ref": "#/components/responses/ServiceUnavailable"}
    response = contract["components"]["responses"]["StaleRecipeFacetCursor"]
    example = response["content"]["application/problem+json"]["example"]
    assert example["type"].endswith("/stale_recipe_facet_cursor")
    assert example["errorCategory"] == "stale_recipe_facet_cursor"
