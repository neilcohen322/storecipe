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


def test_automatic_release_waits_for_public_production_contract() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for variable in (
        "PUBLIC_ORIGIN",
        "AUTH0_DOMAIN",
        "AUTH0_PUBLIC_CLIENT_ID",
        "AUTH0_API_AUDIENCE",
        "MCP_RESOURCE_URL",
        "LEGAL_OPERATOR_NAME",
        "PRIVACY_CONTACT_EMAIL",
        "LEGAL_EFFECTIVE_DATE",
    ):
        assert f"vars.{variable} != ''" in text


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
    for variable in (
        "EXPO_PUBLIC_AUTH0_DOMAIN",
        "EXPO_PUBLIC_AUTH0_CLIENT_ID",
        "EXPO_PUBLIC_AUTH0_AUDIENCE",
        "EXPO_PUBLIC_CATALOG_API_URL",
        "EXPO_PUBLIC_INGESTION_API_URL",
        "EXPO_PUBLIC_LEGAL_OPERATOR_NAME",
        "EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL",
        "EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE",
    ):
        assert variable in text
    assert "EXPO_PUBLIC_CLIENT_SECRET" not in text


def test_release_scans_exact_four_local_images_before_push_and_manifest() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    build = text.index("- name: Build four immutable images")
    scan = text.index("- name: Scan the exact four release images")
    push = text.index("- name: Push four immutable images")
    manifest = text.index("- name: Build and validate release manifest")

    assert build < scan < push < manifest
    assert "aquasecurity/setup-trivy@e07451d2e059ed86c2870430ea286b3a9e0bf241" in text
    assert "version: v0.68.2" in text
    assert (
        "trivy image --scanners vuln --ignore-unfixed --severity HIGH,CRITICAL --exit-code 1"
        in text
    )
    assert "[[ ${#images[@]} -eq 4 ]]" in text
    scanned = text[scan:push]
    for image in (
        "storecipe-web:$COMMIT",
        "storecipe-catalog:$COMMIT",
        "storecipe-ingestion:$COMMIT",
        "storecipe-mcp:$COMMIT",
    ):
        assert scanned.count(image) == 1
    assert "docker push" not in scanned
