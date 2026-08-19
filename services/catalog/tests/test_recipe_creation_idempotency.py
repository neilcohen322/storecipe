import pytest
from pydantic import TypeAdapter, ValidationError

from catalog.recipe_creation_idempotency import (
    IdempotencyKey,
    canonical_recipe_payload,
    recipe_payload_hash,
)
from catalog.schemas import RecipeCreate


def test_idempotency_key_accepts_uuid_and_rejects_unsafe_values() -> None:
    adapter = TypeAdapter(IdempotencyKey)
    assert adapter.validate_python("550e8400-e29b-41d4-a716-446655440000")
    for value in ("short", "contains space", "x" * 129, "slash/value"):
        with pytest.raises(ValidationError):
            adapter.validate_python(value)


def test_recipe_hash_includes_canonical_name() -> None:
    base = RecipeCreate.model_validate(
        {
            "title": "Soup",
            "ingredients": [
                {
                    "rawText": "1 cup water",
                    "name": "water",
                    "canonicalName": "water",
                    "quantity": "1.0",
                }
            ],
            "instructions": ["Boil."],
            "tags": [],
        }
    )
    changed = base.model_copy(
        update={"ingredients": [base.ingredients[0].model_copy(update={"canonical_name": "H2O"})]}
    )
    assert recipe_payload_hash(base) != recipe_payload_hash(changed)


def test_recipe_hash_treats_unicode_equivalent_canonical_names_as_distinct_inputs() -> None:
    composed = RecipeCreate.model_validate(
        {
            "title": "Soup",
            "ingredients": [
                {
                    "rawText": "1 cup water",
                    "name": "water",
                    "canonicalName": "Cafe\u0301",
                }
            ],
            "instructions": ["Boil."],
            "tags": [],
        }
    )
    precomposed = RecipeCreate.model_validate(
        {
            "title": "Soup",
            "ingredients": [
                {
                    "rawText": "1 cup water",
                    "name": "water",
                    "canonicalName": "caf\u00e9",
                }
            ],
            "instructions": ["Boil."],
            "tags": [],
        }
    )

    assert recipe_payload_hash(composed) != recipe_payload_hash(precomposed)


def test_recipe_hash_uses_validated_canonical_payload() -> None:
    first = RecipeCreate.model_validate(
        {
            "title": "Soup",
            "ingredients": [
                {
                    "rawText": "1 cup water",
                    "name": "water",
                    "canonicalName": "water",
                    "quantity": "1.0",
                }
            ],
            "instructions": ["Boil."],
            "tags": [],
        }
    )
    equivalent = RecipeCreate.model_validate(first.model_dump(mode="json", by_alias=True))
    changed = first.model_copy(update={"title": "Stew"})

    assert canonical_recipe_payload(first) == canonical_recipe_payload(equivalent)
    assert recipe_payload_hash(first) == recipe_payload_hash(equivalent)
    assert recipe_payload_hash(first) != recipe_payload_hash(changed)
    assert len(recipe_payload_hash(first)) == 64
