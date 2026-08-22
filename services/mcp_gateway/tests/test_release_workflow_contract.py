from pathlib import Path

ROOT = Path(__file__).parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def test_release_follows_green_master_and_supports_manual_republish() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_run:" in text
    assert "workflows: [CI]" in text
    assert "branches: [master]" in text
    assert "workflow_dispatch:" in text
    assert "No successful CI run exists for requested commit" in text
    assert "git merge-base --is-ancestor" in text


def test_release_has_minimal_permissions_and_pinned_actions() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "contents: read" in text
    assert "actions: read" in text
    assert "packages: write" in text
    assert "@v" not in text
    assert "pull_request_target" not in text


def test_release_builds_four_images_and_emits_strict_manifest() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for dockerfile in (
        "infra/production/Dockerfile.web",
        "services/catalog/Dockerfile",
        "services/ingestion/Dockerfile",
        "services/mcp_gateway/Dockerfile",
    ):
        assert dockerfile in text
    assert text.count("docker push") == 1
    assert "@sha256:[0-9a-f]{64}" in text
    assert "scripts/release/build_manifest.py" in text
    assert "scripts/release/validate_manifest.py" in text
    assert "release-manifest.json" in text


def test_frontend_build_receives_only_public_configuration() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "EXPO_PUBLIC_AUTH0_DOMAIN" in text
    assert "EXPO_PUBLIC_AUTH0_CLIENT_ID" in text
    assert "EXPO_PUBLIC_AUTH0_AUDIENCE" in text
    assert "EXPO_PUBLIC_CATALOG_API_URL" in text
    assert "EXPO_PUBLIC_INGESTION_API_URL" in text
    assert "EXPO_PUBLIC_CLIENT_SECRET" not in text
