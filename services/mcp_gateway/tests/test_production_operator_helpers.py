import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
BUNDLE = ROOT / "scripts" / "deploy" / "build_runtime_bundle.ps1"
MCP_SMOKE = ROOT / "scripts" / "smoke-mcp-auth.ps1"
ENV_EXAMPLE = ROOT / ".env.example"
ENVIRONMENT_CONTRACT = ROOT / "contracts" / "environment.md"


def test_runtime_bundle_helper_is_outside_repo_and_secret_safe() -> None:
    text = BUNDLE.read_text(encoding="utf-8")
    assert "OutputPath must be outside the repository" in text
    assert "RandomNumberGenerator" in text
    assert "INGESTION_PAYLOAD_KEYRING" in text
    assert "STORECIPE_INPUT_MCP_OBO_CLIENT_SECRET" in text
    assert "STORECIPE_INPUT_CATALOG_M2M_CLIENT_SECRET" in text
    assert "STORECIPE_INPUT_CATALOG_ACCOUNT_DELETION_CLIENT_SECRET" in text
    assert "STORECIPE_INPUT_OPENROUTER_API_KEY" in text
    assert "shell-sensitive characters" in text
    assert "Values were not printed" in text
    assert "STORECIPE_WEB_IMAGE" not in text
    assert "A-Za-z0-9._+/=-" in text
    assert "._~" not in text


def test_runtime_bundle_validates_legal_inputs_and_wires_deletion_client() -> None:
    text = BUNDLE.read_text(encoding="utf-8")
    for parameter in (
        "CatalogAccountDeletionClientId",
        "LegalOperatorName",
        "PrivacyContactEmail",
        "LegalEffectiveDate",
        "AccountDeletionJournalBucket",
    ):
        assert f"${parameter}" in text
    for variable in (
        "CATALOG_ACCOUNT_DELETION_TOKEN_URL",
        "CATALOG_ACCOUNT_DELETION_CLIENT_ID",
        "CATALOG_ACCOUNT_DELETION_CLIENT_SECRET",
        "CATALOG_ACCOUNT_DELETION_INTERNAL_AUDIENCE",
        "CATALOG_ACCOUNT_DELETION_AUTH0_AUDIENCE",
        "CATALOG_ACCOUNT_DELETION_AUTH0_MANAGEMENT_BASE_URL",
        "CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET",
    ):
        assert f'"{variable}=' in text
    assert "accounts:internal:delete" not in text
    assert "delete:users" not in text


def test_legal_and_deletion_environment_names_are_documented() -> None:
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    contract = ENVIRONMENT_CONTRACT.read_text(encoding="utf-8")
    for variable in (
        "EXPO_PUBLIC_LEGAL_OPERATOR_NAME",
        "EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL",
        "EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE",
        "CATALOG_ACCOUNT_DELETION_TOKEN_URL",
        "CATALOG_ACCOUNT_DELETION_CLIENT_ID",
        "CATALOG_ACCOUNT_DELETION_CLIENT_SECRET",
        "CATALOG_ACCOUNT_DELETION_INTERNAL_AUDIENCE",
        "CATALOG_ACCOUNT_DELETION_AUTH0_AUDIENCE",
        "CATALOG_ACCOUNT_DELETION_AUTH0_MANAGEMENT_BASE_URL",
        "CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET",
    ):
        assert f"{variable}=" in env_example
        assert f"`{variable}`" in contract
    assert "independently by audience" in contract


def test_runtime_bundle_validate_only_prints_no_supplied_values() -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not installed")

    supplied = (
        "https://storecipe.test",
        "tenant.storecipe.test",
        "mcp-client-id",
        "catalog-client-id",
        "deletion-client-id",
        "Storecipe Test Operator",
        "privacy@storecipe.test",
        "2026-01-01",
        "media-bucket",
        "backup-bucket",
        "journal-bucket",
        "obo-secret",
        "catalog-secret",
        "deletion-secret",
        "openrouter-secret",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "STORECIPE_INPUT_MCP_OBO_CLIENT_SECRET": supplied[11],
            "STORECIPE_INPUT_CATALOG_M2M_CLIENT_SECRET": supplied[12],
            "STORECIPE_INPUT_CATALOG_ACCOUNT_DELETION_CLIENT_SECRET": supplied[13],
            "STORECIPE_INPUT_OPENROUTER_API_KEY": supplied[14],
        }
    )
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BUNDLE),
            "-OutputPath",
            str(Path(os.environ.get("TEMP", ROOT.parent)) / "storecipe-runtime-validate-only.env"),
            "-PublicOrigin",
            supplied[0],
            "-Auth0Domain",
            supplied[1],
            "-McpOboClientId",
            supplied[2],
            "-CatalogM2mClientId",
            supplied[3],
            "-CatalogAccountDeletionClientId",
            supplied[4],
            "-LegalOperatorName",
            supplied[5],
            "-PrivacyContactEmail",
            supplied[6],
            "-LegalEffectiveDate",
            supplied[7],
            "-MediaBucket",
            supplied[8],
            "-BackupBucket",
            supplied[9],
            "-AccountDeletionJournalBucket",
            supplied[10],
            "-ValidateOnly",
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "No file was written and no value was printed" in output
    for value in supplied:
        assert value not in output


def test_mcp_smoke_never_emits_raw_identity_or_tokens() -> None:
    text = MCP_SMOKE.read_text(encoding="utf-8")
    assert "STORECIPE_MCP_ACCESS_TOKEN" in text
    assert "STORECIPE_OBO_API_ACCESS_TOKEN" in text
    assert "subjectMatches" in text
    assert "actPresent" in text
    assert "audienceLabel" in text
    assert "expiryBucket" in text
    assert "Write-Host $mcpToken" not in text
    assert "Write-Host $delegatedApiToken" not in text
    assert "email" not in text.lower()
    assert "audience/OBO proof cannot be skipped in -Live mode" in text
    assert "exit 1" in text


def test_mcp_smoke_requires_exact_six_tool_evidence() -> None:
    text = MCP_SMOKE.read_text(encoding="utf-8")
    for tool in (
        "query_recipes",
        "get_recipe",
        "create_recipe",
        "rate_recipe",
        "list_recipe_query_options",
        "resolve_recipe_query_selections",
    ):
        assert f"'{tool}'" in text
    assert "exactly the six approved tools" in text
