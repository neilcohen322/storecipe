import importlib.util
import inspect
from pathlib import Path

from sqlalchemy import CheckConstraint

from catalog.models import Recipe

MIGRATION_FILENAME = "20260824_01_recipe_personalization.py"


def load_migration() -> object:
    path = Path(__file__).parents[1] / "migrations" / "versions" / MIGRATION_FILENAME
    spec = importlib.util.spec_from_file_location("recipe_personalization_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_recipe_personalization_migration_is_additive_and_reversible() -> None:
    migration = load_migration()
    upgrade = inspect.getsource(migration.upgrade)
    downgrade = inspect.getsource(migration.downgrade)

    assert migration.down_revision == "20260812_01"
    assert '"favorite"' in upgrade
    assert "server_default=sa.false()" in upgrade
    assert '"personal_notes"' in upgrade
    assert '"last_cooked_at"' in upgrade
    assert "sa.DateTime(timezone=True)" in upgrade
    assert "ck_recipes_personal_notes_length" in upgrade
    assert "length(personal_notes) <= 5000" in upgrade
    assert "ix_recipes_user_favorite" in upgrade
    assert 'postgresql_where=sa.text("favorite IS TRUE")' in upgrade
    assert downgrade.count("op.drop_column") == 3


def test_recipe_model_declares_personalization_check_and_partial_index() -> None:
    check_names = {
        constraint.name
        for constraint in Recipe.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    favorite_index = next(
        index for index in Recipe.__table__.indexes if index.name == "ix_recipes_user_favorite"
    )

    assert "ck_recipes_personal_notes_length" in check_names
    assert [column.name for column in favorite_index.columns] == ["user_id"]
    assert str(favorite_index.dialect_options["postgresql"]["where"]) == "favorite IS TRUE"
