from pathlib import Path

ROOT = Path(__file__).parents[3]
PRODUCTION_CADDY = ROOT / "infra" / "production" / "Caddyfile"
PRODUCTION_COMPOSE = ROOT / "infra" / "production" / "compose.yaml"
WEB_DOCKERFILE = ROOT / "infra" / "production" / "Dockerfile.web"
PRODUCTION_BUNDLE = ROOT / "apps" / "web" / "scripts" / "verify-production-bundle.mjs"
PLAYWRIGHT_CONFIG = ROOT / "apps" / "web" / "playwright.config.ts"


def test_edge_routes_are_ordered_and_internal_is_hidden() -> None:
    caddy = PRODUCTION_CADDY.read_text(encoding="utf-8")
    assert "{$PUBLIC_HOST}" in caddy
    assert caddy.index("handle /v1/imports*") < caddy.index("handle /v1/*")
    assert "reverse_proxy ingestion-api:8001" in caddy
    assert "reverse_proxy catalog-api:8000" in caddy
    assert "reverse_proxy mcp-gateway:8002" in caddy
    assert "handle /internal" not in caddy
    assert "handle /api" in caddy
    assert "respond 404" in caddy


def test_cover_upload_has_eight_mb_edge_limit() -> None:
    caddy = PRODUCTION_CADDY.read_text(encoding="utf-8")
    assert "@coverUpload" in caddy
    assert "max_size 8MB" in caddy
    assert caddy.index("request_body @coverUpload") < caddy.index("handle /v1/*")


def test_edge_redacts_auth_cookie_and_query_material() -> None:
    caddy = PRODUCTION_CADDY.read_text(encoding="utf-8")
    assert "request>headers>Authorization delete" in caddy
    assert "request>headers>Cookie delete" in caddy
    assert "request>uri replace REDACTED" in caddy


def test_edge_health_is_local_http_without_claiming_public_acme_host() -> None:
    caddy = PRODUCTION_CADDY.read_text(encoding="utf-8")
    compose = PRODUCTION_COMPOSE.read_text(encoding="utf-8")
    assert "http://127.0.0.1, http://localhost {" in caddy
    assert "respond 200" in caddy
    assert caddy.index("http://127.0.0.1, http://localhost {") < caddy.index("{$PUBLIC_HOST} {")
    assert '"http://127.0.0.1/"' in compose
    assert '"http://localhost/"' not in compose


def test_web_image_has_public_inputs_and_no_secret_build_arg() -> None:
    dockerfile = WEB_DOCKERFILE.read_text(encoding="utf-8")
    for name in (
        "EXPO_PUBLIC_AUTH0_DOMAIN",
        "EXPO_PUBLIC_AUTH0_CLIENT_ID",
        "EXPO_PUBLIC_AUTH0_AUDIENCE",
        "EXPO_PUBLIC_CATALOG_API_URL",
        "EXPO_PUBLIC_INGESTION_API_URL",
    ):
        assert f"ARG {name}" in dockerfile
    assert "ARG SECRET" not in dockerfile.upper()
    assert "caddy:2.11.4-alpine" in dockerfile
    assert "COPY --from=web-build" in dockerfile


def test_production_and_e2e_exports_clear_environment_sensitive_metro_cache() -> None:
    production = PRODUCTION_BUNDLE.read_text(encoding="utf-8")
    playwright = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    assert "delete environment.EXPO_PUBLIC_E2E_MODE" in production
    assert '"--platform", "web", "--clear"' in production
    assert 'EXPO_PUBLIC_E2E_MODE: "true"' in playwright
    assert "expo export --platform web --clear" in playwright
