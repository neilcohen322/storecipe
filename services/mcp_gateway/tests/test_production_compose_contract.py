import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
COMPOSE = ROOT / "infra" / "production" / "compose.yaml"
WEB_DOCKERFILE = ROOT / "infra" / "production" / "Dockerfile.web"
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
    assert (
        "postgres:17-alpine@sha256:"
        "18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73"
    ) in text
    assert (
        text.count(
            "redis:7.4-alpine@sha256:"
            "ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf"
        )
        == 2
    )


def test_web_base_image_is_digest_pinned() -> None:
    text = WEB_DOCKERFILE.read_text(encoding="utf-8")
    assert (
        "FROM caddy:2.11.4-alpine@sha256:"
        "5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
    ) in text
    assert (
        "FROM node:24-alpine@sha256:"
        "d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43"
    ) in text


def test_service_base_images_are_digest_pinned() -> None:
    pinned = (
        "FROM ghcr.io/astral-sh/uv:0.11.28-python3.13-trixie-slim@sha256:"
        "08477888ac23d6cfbeb8c7dc6fc70cf297fd38b7bf35522be33ce832750ca242"
    )
    for relative in (
        "services/catalog/Dockerfile",
        "services/ingestion/Dockerfile",
        "services/mcp_gateway/Dockerfile",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert pinned in text


def test_only_edge_publishes_ports() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert text.count("ports:") == 1
    assert '"80:80"' in text
    assert '"443:443"' in text


def test_edge_receives_auth0_issuer_for_csp_expansion() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    edge = text[text.index("  edge:") : text.index("  postgres:")]
    assert "AUTH0_ISSUER: ${AUTH0_ISSUER:?required}" in edge


def test_catalog_receives_dedicated_account_deletion_m2m_contract() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    catalog = text[text.index("  catalog-api:") : text.index("  catalog-migrate:")]
    for variable in (
        "CATALOG_ACCOUNT_DELETION_TOKEN_URL",
        "CATALOG_ACCOUNT_DELETION_CLIENT_ID",
        "CATALOG_ACCOUNT_DELETION_CLIENT_SECRET",
        "CATALOG_ACCOUNT_DELETION_INTERNAL_AUDIENCE",
        "CATALOG_ACCOUNT_DELETION_AUTH0_AUDIENCE",
        "CATALOG_ACCOUNT_DELETION_AUTH0_MANAGEMENT_BASE_URL",
    ):
        assert f"{variable}: ${{{variable}:?required}}" in catalog


def test_web_image_requires_non_placeholder_legal_build_inputs() -> None:
    text = WEB_DOCKERFILE.read_text(encoding="utf-8")
    for variable in (
        "EXPO_PUBLIC_LEGAL_OPERATOR_NAME",
        "EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL",
        "EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE",
    ):
        assert f"ARG {variable}" in text
        assert f"{variable}=${{{variable}}}" in text
    assert "*'<'*|*'>'*" in text
    assert "^[^[:space:]@]+@[^[:space:]@]+\\.[^[:space:]@]+$" in text
    assert "^[0-9]{4}-[0-9]{2}-[0-9]{2}$" in text


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
