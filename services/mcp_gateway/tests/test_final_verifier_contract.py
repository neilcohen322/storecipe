from pathlib import Path

ROOT = Path(__file__).parents[3]
VERIFY = ROOT / "scripts" / "verify.ps1"


def test_verifier_covers_offline_production_contracts() -> None:
    text = VERIFY.read_text(encoding="utf-8")
    assert "infra/production/compose.yaml" in text
    assert "caddy:2.11.4-alpine" in text
    assert "bash:5.3 bash -n" in text
    assert "hashicorp/terraform:1.15" in text
    assert "fmt -check -recursive infra/terraform" in text
    assert "init -backend=false" in text
    assert "Dockerfile.web" in text
    assert "docker compose build catalog-api" in text
    assert "docker compose build ingestion-api" in text
    assert "docker compose build mcp-gateway" in text


def test_live_checks_are_explicitly_opt_in() -> None:
    text = VERIFY.read_text(encoding="utf-8")
    assert "RUN_PRODUCTION_LIVE_CHECKS" in text
    assert "smoke-mcp-auth.ps1" in text
    assert "Write-Unverified 'Live production checks" in text
    assert "optional or live checks remain explicitly UNVERIFIED" in text
    assert "All checks passed." not in text
    assert "pnpm run test:production-bundle" in text
