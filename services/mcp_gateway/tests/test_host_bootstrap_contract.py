from pathlib import Path

ROOT = Path(__file__).parents[3]
INSTALL = ROOT / "scripts" / "deploy" / "install_host.sh"
STARTUP = ROOT / "infra" / "terraform" / "production" / "templates" / "startup.sh.tftpl"
RUNTIME_OPERATION = ROOT / "scripts" / "deploy" / "run_with_runtime_env.sh"


def test_host_uses_retained_data_disk_for_docker_and_two_gb_swap() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert "/dev/disk/by-id/google-storecipe-data" in text
    assert '"data-root":"/var/lib/storecipe/docker"' in text
    assert "fallocate -l 2G" in text
    assert "chmod 0600" in text
    assert "/swapfile" in text


def test_host_install_is_idempotent_and_root_owned() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert "if [[ ! -f /etc/apt/keyrings/docker.asc ]]" in text
    assert "if [[ ! -e /swapfile ]]" in text
    assert "/opt/storecipe/releases /opt/storecipe/current" in text
    assert "-o root -g root" in text
    assert "systemctl enable --now storecipe-backup.timer storecipe-media-reconcile.timer" in text


def test_scheduled_operations_fetch_and_remove_runtime_secret() -> None:
    text = RUNTIME_OPERATION.read_text(encoding="utf-8")
    assert "gcloud secrets versions access latest" in text
    assert "chmod 0600" in text
    assert "trap cleanup EXIT" in text
    assert 'rm -f "$RUNTIME_ENV"' in text
    assert 'echo "$POSTGRES_ADMIN_PASSWORD"' not in text


def test_startup_metadata_contains_identifiers_but_no_secret_payload() -> None:
    text = STARTUP.read_text(encoding="utf-8")
    assert "RUNTIME_SECRET_NAME=${runtime_secret_name}" in text
    assert "secret_data" not in text
    assert "PASSWORD=" not in text
    assert "BEGIN PRIVATE" not in text
