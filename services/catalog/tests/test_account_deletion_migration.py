from pathlib import Path

MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "versions" / "20260824_02_account_deletions.py"
)


def test_account_deletion_migration_is_additive_and_chained_after_personalization() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "20260824_02"' in source
    assert 'down_revision: str | None = "20260824_01"' in source
    assert '"account_deletions"' in source
    assert '"auth_subject"' in source
    assert '"catalog_user_id"' in source
    assert '"media_snapshot"' in source
    assert '"lease_expires_at"' in source
    assert '"ix_account_deletions_due"' in source
    assert '"ix_account_deletions_expiry"' in source


def test_journal_committed_migration_is_chained_after_account_deletions() -> None:
    migration = (
        Path(__file__).parents[1] / "migrations" / "versions" / "20260826_01_journal_committed.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert 'revision: str = "20260826_01"' in source
    assert 'down_revision: str | None = "20260824_02"' in source
    assert '"journal_committed"' in source
