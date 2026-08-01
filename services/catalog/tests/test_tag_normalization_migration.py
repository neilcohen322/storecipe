import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260801_02_normalized_tag_names.py"
    )
    spec = importlib.util.spec_from_file_location("normalized_tag_names_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tag_migration_merges_unicode_equivalent_names_and_preserves_links() -> None:
    migration = _load_migration()
    canonical_id = UUID("00000000-0000-0000-0000-000000000001")
    duplicate_id = UUID("00000000-0000-0000-0000-000000000002")
    first_recipe = UUID("10000000-0000-0000-0000-000000000001")
    second_recipe = UUID("10000000-0000-0000-0000-000000000002")

    plans = migration._build_tag_migration_plan(
        [(canonical_id, "café"), (duplicate_id, "CAFE\u0301")],
        [(first_recipe, canonical_id), (first_recipe, duplicate_id), (second_recipe, duplicate_id)],
    )

    assert plans == [
        migration.TagMergePlan(
            canonical_id=canonical_id,
            normalized_name="café",
            source_ids=(canonical_id, duplicate_id),
            recipe_ids=(first_recipe, second_recipe),
        )
    ]


def test_tag_migration_merges_casefold_collision_into_exact_normalized_name() -> None:
    migration = _load_migration()
    exact_id = UUID("00000000-0000-0000-0000-000000000009")
    folded_id = UUID("00000000-0000-0000-0000-000000000003")
    recipe_id = UUID("10000000-0000-0000-0000-000000000003")

    plans = migration._build_tag_migration_plan(
        [(folded_id, "straße"), (exact_id, "strasse")],
        [(recipe_id, folded_id)],
    )

    assert plans[0].canonical_id == exact_id
    assert plans[0].normalized_name == "strasse"
    assert plans[0].source_ids == (folded_id, exact_id)
    assert plans[0].recipe_ids == (recipe_id,)


def test_tag_migration_rejects_casefold_expansion_beyond_database_limit() -> None:
    migration = _load_migration()

    with pytest.raises(ValueError, match="exceeds the 64-character tag limit"):
        migration._build_tag_migration_plan(
            [(UUID("00000000-0000-0000-0000-000000000001"), "ß" * 64)],
            [],
        )
