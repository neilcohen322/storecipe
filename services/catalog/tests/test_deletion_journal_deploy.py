from pathlib import Path

ROOT = Path(__file__).parents[3]
DEPLOY = ROOT / "scripts" / "deploy" / "deploy.sh"


def test_deploy_replays_private_deletion_journal_after_both_migrations() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    ingestion_migration = text.index('run_step "Ingestion migration"')
    replay = text.index('run_step "account-deletion journal replay"')
    start = text.index('run_step "start target release"')

    assert ingestion_migration < replay < start
    assert "python -m catalog.deletion_journal_replay" in text
    assert "CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET" in text
    assert "INGESTION_DATABASE_URL" in text
