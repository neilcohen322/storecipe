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


def test_recipe_query_item_match_documents_nullable_recipe_match() -> None:
    contract = _contract()
    match_schema = contract["components"]["schemas"]["RecipeQueryItem"]["properties"]["match"]

    assert match_schema["description"] == (
        "Factual match details; null unless availableIngredient or preferredTag is supplied."
    )
    assert match_schema["anyOf"] == [
        {"$ref": "#/components/schemas/RecipeMatch"},
        {"type": "null"},
    ]


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
