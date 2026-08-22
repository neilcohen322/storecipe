from pathlib import Path

ROOT = Path(__file__).parents[3]
BOOTSTRAP = ROOT / "infra" / "terraform" / "bootstrap"


def combined() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in BOOTSTRAP.glob("*.tf"))


def test_versions_are_bounded() -> None:
    text = (BOOTSTRAP / "versions.tf").read_text(encoding="utf-8")
    assert 'required_version = "~> 1.15.0"' in text
    assert 'version = "~> 7.40.0"' in text


def test_state_bucket_is_private_versioned_and_retained() -> None:
    text = combined()
    assert "uniform_bucket_level_access = true" in text
    assert 'public_access_prevention    = "enforced"' in text
    assert "versioning" in text and "enabled = true" in text
    assert "days_since_noncurrent_time = 30" in text
    assert "force_destroy               = false" in text


def test_wif_is_repository_and_master_restricted_without_keys() -> None:
    text = combined()
    assert 'issuer_uri = "https://token.actions.githubusercontent.com"' in text
    assert "attribute.repository ==" in text
    assert "assertion.ref == 'refs/heads/master'" in text
    assert '"attribute.workflow"    = "assertion.workflow_ref"' in text
    assert ".github/workflows/terraform.yml@refs/heads/master" in text
    assert "roles/iam.workloadIdentityUser" in text
    assert "google_service_account_key" not in text


def test_terraform_identity_can_manage_budget_email_channel() -> None:
    text = combined()
    assert '"roles/monitoring.editor"' in text
