import importlib.util
import inspect
from collections import Counter
from pathlib import Path

from sqlalchemy import UniqueConstraint

from catalog.models import RecipeImage

MIGRATION_PATH = (
    Path(__file__).parents[1] / "migrations" / "versions" / "20260812_01_recipe_cover_images.py"
)


def load_migration(filename: str) -> object:
    path = Path(__file__).parents[1] / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location("recipe_cover_images_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_recipe_image_migration_has_one_to_one_and_bounds() -> None:
    migration = load_migration("20260812_01_recipe_cover_images.py")
    source = inspect.getsource(migration.upgrade)
    assert migration.down_revision == "20260803_01"
    assert '"recipe_images"' in source
    assert "uq_recipe_images_recipe_id" in source
    assert "ck_recipe_images_webp" in source
    assert "ck_recipe_images_byte_size" in source
    assert 'ondelete="CASCADE"' in source
    assert MIGRATION_PATH.exists()


def test_recipe_image_named_uniques_are_declared_once() -> None:
    names = [
        constraint.name
        for constraint in RecipeImage.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    counts = Counter(names)
    assert counts["uq_recipe_images_recipe_id"] == 1
    assert counts["uq_recipe_images_object_key"] == 1
    column_sets = [
        tuple(column.name for column in constraint.columns)
        for constraint in RecipeImage.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert column_sets.count(("recipe_id",)) == 1
    assert column_sets.count(("object_key",)) == 1
