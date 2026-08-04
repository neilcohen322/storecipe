import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations"
    / "versions"
    / "20260802_01_mcp_recipe_creation_idempotency.py"
)


def test_mcp_idempotency_migration_shape() -> None:
    spec = importlib.util.spec_from_file_location("mcp_idempotency_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = MIGRATION_PATH.read_text()

    assert migration.revision == "20260802_01"
    assert migration.down_revision == "20260801_02"
    assert "recipe_creation_idempotency" in source
    assert source.count('ondelete="CASCADE"') == 2
    assert "payload_hash" in source
    assert "idempotency_key" in source
