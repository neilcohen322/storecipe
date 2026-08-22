from pathlib import Path

ROOT = Path(__file__).parents[3]
BACKUP = ROOT / "scripts" / "deploy" / "backup.sh"
RESTORE = ROOT / "scripts" / "deploy" / "restore_verify.sh"
LOCAL_PROOF = ROOT / "scripts" / "deploy" / "verify_restore_local.sh"


def test_backup_is_custom_format_checksummed_and_cloud_native() -> None:
    text = BACKUP.read_text(encoding="utf-8")
    assert "pg_dump" in text and "--format=custom" in text
    assert "sha256sum" in text
    assert "date -u +%Y%m%dT%H%M%SZ" in text
    assert "storage cp" in text
    assert "gsutil" not in text
    assert "/daily/" in text and "/weekly/" in text
    assert "prune_prefix daily 7" in text
    assert "prune_prefix weekly 4" in text
    assert 'echo "$POSTGRES_ADMIN_PASSWORD"' not in text


def test_restore_is_disposable_and_always_cleaned() -> None:
    text = RESTORE.read_text(encoding="utf-8")
    assert "sha256sum --check --strict" in text
    assert "postgres:17-alpine" in text
    assert "pg_restore" in text and "--exit-on-error" in text
    assert "catalog.alembic_version_catalog" in text
    assert "ingestion.alembic_version_ingestion" in text
    assert "docker rm -f" in text
    assert "trap cleanup EXIT" in text


def test_local_restore_proof_uses_disposable_synthetic_data() -> None:
    text = LOCAL_PROOF.read_text(encoding="utf-8")
    assert "postgres:17-alpine" in text
    assert "storecipe-backup-source" in text
    assert "docker rm -f" in text
    assert "catalog.alembic_version_catalog" in text
    assert "ingestion.alembic_version_ingestion" in text
    assert "gs://fake-storecipe/" in text
