from pathlib import Path

ROOT = Path(__file__).parents[3]
BUNDLE = ROOT / "scripts" / "deploy" / "build_runtime_bundle.ps1"
MCP_SMOKE = ROOT / "scripts" / "smoke-mcp-auth.ps1"


def test_runtime_bundle_helper_is_outside_repo_and_secret_safe() -> None:
    text = BUNDLE.read_text(encoding="utf-8")
    assert "OutputPath must be outside the repository" in text
    assert "RandomNumberGenerator" in text
    assert "INGESTION_PAYLOAD_KEYRING" in text
    assert "STORECIPE_INPUT_MCP_OBO_CLIENT_SECRET" in text
    assert "STORECIPE_INPUT_CATALOG_M2M_CLIENT_SECRET" in text
    assert "STORECIPE_INPUT_OPENROUTER_API_KEY" in text
    assert "shell-sensitive characters" in text
    assert "Values were not printed" in text
    assert "STORECIPE_WEB_IMAGE" not in text


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
