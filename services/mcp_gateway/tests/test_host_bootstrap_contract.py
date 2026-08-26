import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
INSTALL = ROOT / "scripts" / "deploy" / "install_host.sh"
STARTUP = ROOT / "infra" / "terraform" / "production" / "templates" / "startup.sh.tftpl"
RUNTIME_OPERATION = ROOT / "scripts" / "deploy" / "run_with_runtime_env.sh"
BOOTSTRAP_TLS = ROOT / "scripts" / "deploy" / "start_bootstrap_tls.sh"


def test_host_uses_retained_data_disk_for_docker_and_two_gb_swap() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert "/dev/disk/by-id/google-storecipe-data" in text
    assert '"data-root":"/var/lib/storecipe/docker"' in text
    assert "fallocate -l 2G" in text
    assert "chmod 0600" in text
    assert "/swapfile" in text


def test_host_install_is_idempotent_and_root_owned() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    startup = STARTUP.read_text(encoding="utf-8")
    assert "if [[ ! -f /etc/apt/keyrings/docker.asc ]]" in text
    assert "if [[ ! -e /swapfile ]]" in text
    assert "/opt/storecipe/releases /opt/storecipe/current" in text
    assert "-o root -g root" in text
    assert "systemctl enable --now storecipe-backup.timer storecipe-media-reconcile.timer" in text
    assert "postgresql-client python3" in text
    assert "postgresql-client python3" in startup
    assert "google-cloud-ops-agent" in startup
    assert "google-cloud-ops-agent" in text
    assert "dpkg-query -W -f=" in text
    assert "dpkg-query -W -f=" in startup
    assert "${Status}" in text
    assert "$${Status}" in startup
    assert "install ok installed" in text
    assert "install ok installed" in startup
    assert "list-unit-files google-cloud-ops-agent" not in text
    assert "list-unit-files google-cloud-ops-agent" not in startup
    assert "add-google-cloud-ops-agent-repo.sh" not in text
    assert "add-google-cloud-ops-agent-repo.sh" not in startup
    assert "google-cloud-ops-agent-${VERSION_CODENAME}-all" in text
    assert "google-cloud-ops-agent-$VERSION_CODENAME-all" in startup
    assert "signed-by=/usr/share/keyrings/cloud.google.gpg" in text
    assert "signed-by=/usr/share/keyrings/cloud.google.gpg" in startup


def test_scheduled_operations_fetch_and_remove_runtime_secret() -> None:
    text = RUNTIME_OPERATION.read_text(encoding="utf-8")
    assert "gcloud secrets versions access latest" in text
    assert "chmod 0600" in text
    assert "trap cleanup EXIT" in text
    assert 'rm -f "$RUNTIME_ENV"' in text
    assert 'echo "$POSTGRES_ADMIN_PASSWORD"' not in text
    assert "shell-sensitive syntax" in text
    assert "grep -Eqv" in text


def test_startup_metadata_contains_identifiers_but_no_secret_payload() -> None:
    text = STARTUP.read_text(encoding="utf-8")
    assert "RUNTIME_SECRET_NAME=${runtime_secret_name}" in text
    assert "secret_data" not in text
    assert "PASSWORD=" not in text
    assert "BEGIN PRIVATE" not in text


def test_bootstrap_tls_is_hostname_bounded_and_reuses_production_caddy_data() -> None:
    text = BOOTSTRAP_TLS.read_text(encoding="utf-8")
    assert "start_bootstrap_tls.sh must run as root" in text
    assert "^[a-z0-9]" in text
    assert "-p 80:80 -p 443:443" in text
    assert "storecipe-production_caddy-data:/data" in text
    assert "caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648" in text
    assert "Certificate issuance is asynchronous" in text


def _usable_bash() -> str | None:
    candidates: list[str] = []
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        candidates.append(str(git_bash))
    which = shutil.which("bash")
    if which is not None and which not in candidates:
        candidates.append(which)
    for bash in candidates:
        try:
            result = subprocess.run(
                [bash, "-c", "echo ok"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and b"ok" in result.stdout:
            return bash
    return None


def test_ops_agent_installs_on_fresh_debian_when_unit_is_absent() -> None:
    """systemctl list-unit-files can exit 0 with zero matches; dpkg-query must decide."""
    bash = _usable_bash()
    if bash is None:
        pytest.skip("usable bash required for Debian host simulation")
    installed_status = "install ok installed"
    gate = textwrap.dedent(
        f"""\
        set -euo pipefail
        status="$(dpkg-query -W -f='${{Status}}' google-cloud-ops-agent 2>/dev/null || true)"
        if [ "$status" != "{installed_status}" ]; then
          echo INSTALL
        else
          echo SKIP
        fi
        """
    )

    def run_gate(stub: str) -> str:
        script = textwrap.dedent(stub) + "\n" + gate
        result = subprocess.run(
            [bash, "-s"],
            check=True,
            capture_output=True,
            input=script.replace("\r\n", "\n").encode("utf-8"),
        )
        return result.stdout.decode().strip()

    missing = run_gate(
        """\
        function dpkg-query { return 1; }
        function systemctl { return 0; }
        if systemctl list-unit-files google-cloud-ops-agent.service >/dev/null 2>&1; then
          echo UNIT_SKIP
        else
          echo UNIT_INSTALL
        fi
        """
    )
    removed = run_gate("function dpkg-query { echo 'deinstall ok config-files'; }")
    installed = run_gate("function dpkg-query { echo 'install ok installed'; }")

    assert missing.splitlines() == ["UNIT_SKIP", "INSTALL"]
    assert removed == "INSTALL"
    assert installed == "SKIP"
