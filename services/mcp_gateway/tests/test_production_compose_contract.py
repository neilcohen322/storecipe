import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
COMPOSE = ROOT / "infra" / "production" / "compose.yaml"
POSTGRES_INIT = ROOT / "infra" / "production" / "postgres-init" / "001-production-roles.sh"


def test_application_images_are_required_full_digests() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "build:" not in text
    for variable in (
        "STORECIPE_WEB_IMAGE",
        "STORECIPE_CATALOG_IMAGE",
        "STORECIPE_INGESTION_IMAGE",
        "STORECIPE_MCP_IMAGE",
    ):
        assert re.search(rf"image: \$\{{{variable}:\?", text)


def test_only_edge_publishes_ports() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert text.count("ports:") == 1
    assert '"80:80"' in text
    assert '"443:443"' in text


def test_services_are_bounded_and_operationally_configured() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert text.count("mem_limit:") == 10
    assert text.count("healthcheck:") == 10
    assert "max-size: 10m" in text
    assert 'max-file: "3"' in text
    assert "--concurrency=1" in text
    assert '--maxmemory-policy", "noeviction' in text
    assert '--appendonly", "yes' in text


def test_production_postgres_has_no_embedded_password() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    init = POSTGRES_INIT.read_text(encoding="utf-8")
    assert "local_admin_only" not in compose + init
    assert "local_catalog_only" not in compose + init
    assert "local_ingestion_only" not in compose + init
    assert "CATALOG_DB_PASSWORD:?required" in compose
    assert "INGESTION_DB_PASSWORD:?required" in compose
    assert "PASSWORD :'catalog_password'" in init
    assert "GRANT CREATE ON DATABASE" not in init
