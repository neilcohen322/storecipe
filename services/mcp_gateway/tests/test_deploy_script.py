from pathlib import Path

import yaml

ROOT = Path(__file__).parents[3]
DEPLOY = ROOT / "scripts" / "deploy" / "deploy.sh"
SMOKE = ROOT / "scripts" / "deploy" / "smoke_public.py"
COMPOSE = ROOT / "infra" / "production" / "compose.yaml"
CADDY = ROOT / "infra" / "production" / "Caddyfile"


def test_deploy_is_locked_and_validates_before_mutating() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert "flock" in text
    assert "/var/lock/storecipe-deploy.lock" in text
    assert "validate_manifest.py" in text
    assert "DISK_REQUIRED_KB=$((5 * 1024 * 1024))" in text
    assert "swapon --show" in text
    assert "gcloud secrets versions access latest" in text
    assert 'chmod 0600 "$RUNTIME_ENV"' in text
    assert "unsupported or shell-sensitive syntax" in text
    assert "grep -Eqv" in text
    assert "docker compose" in text and "config --quiet" in text


def test_deploy_orders_backup_pull_migrations_and_start() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    backup = text.index('run_step "pre-deployment backup"')
    pull = text.index('run_step "pull immutable images"')
    catalog = text.index('run_step "Catalog migration"')
    ingestion = text.index('run_step "Ingestion migration"')
    start = text.index('run_step "start target release"')
    assert backup < pull < catalog < ingestion < start
    assert "scripts/deploy/backup.sh" in text
    assert "catalog-migrate" in text
    assert "ingestion-migrate" in text
    assert "wait_for_healthy" in text
    assert "smoke_public.py" in text


def test_deploy_captures_previous_images_and_never_downgrades_database() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert "previous-release-manifest.json" in text
    assert "rollback_images" in text
    assert "PREVIOUS_MANIFEST" in text
    assert "--force-recreate" in text
    assert "alembic downgrade" not in text
    assert " downgrade " not in text


def test_migration_failure_requires_backup_restore_before_retry() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert "MIGRATIONS_APPLIED=none" in text
    assert "migration_failed Catalog" in text
    assert "migration_failed Ingestion" in text
    assert "Do not retry deployment or start the target stack" in text
    assert "Restore the latest pre-deployment backup" in text


def test_deploy_retries_local_https_while_caddy_obtains_certificate() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    function = text[text.index("wait_for_local_https()") : text.index("wait_for_service_health()")]
    assert "SECONDS + 180" in function
    assert "sleep 5" in function
    assert '--resolve "$PUBLIC_HOST:443:127.0.0.1"' in function
    assert 'run_step "local Host routing" wait_for_local_https' in text


def test_compose_has_explicit_one_off_migration_services() -> None:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    catalog = data["services"]["catalog-migrate"]
    ingestion = data["services"]["ingestion-migrate"]
    assert catalog["profiles"] == ["migration"]
    assert ingestion["profiles"] == ["migration"]
    assert catalog["restart"] == "no"
    assert ingestion["restart"] == "no"
    assert catalog["command"][-2:] == ["upgrade", "head"]
    assert ingestion["command"][-2:] == ["upgrade", "head"]


def test_public_smoke_checks_tls_auth_challenge_and_metadata() -> None:
    text = SMOKE.read_text(encoding="utf-8")
    assert "HTTPSConnection" in text
    assert 'expect_status("web", "/", 200)' in text
    assert 'expect_status("Catalog protection", "/v1/recipes", 401)' in text
    assert '"MCP protection",' in text and '"/mcp",' in text
    assert "WWW-Authenticate" in text
    assert "/.well-known/oauth-protected-resource/mcp" in text
    assert '"recipes:read", "recipes:write", "ratings:write"' in text
    assert "STORECIPE_SMOKE_ACCESS_TOKEN" in text


def test_edge_exposes_mcp_resource_metadata() -> None:
    text = CADDY.read_text(encoding="utf-8")
    assert "handle /.well-known/oauth-protected-resource/mcp* {" in text
