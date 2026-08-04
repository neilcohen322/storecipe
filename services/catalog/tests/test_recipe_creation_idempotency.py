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


def test_recipe_hash_uses_validated_canonical_payload() -> None:
    first = RecipeCreate.model_validate(
        {
            "title": "Soup",
            "ingredients": [{"rawText": "1 cup water", "name": "water", "quantity": "1.0"}],
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
