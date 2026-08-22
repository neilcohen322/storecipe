from pathlib import Path


ROOT = Path(__file__).parents[3]
CI = ROOT / ".github" / "workflows" / "ci.yml"
TERRAFORM = ROOT / ".github" / "workflows" / "terraform.yml"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"


def test_ci_preserves_five_protected_check_names() -> None:
    text = CI.read_text(encoding="utf-8")
    for job in ("backend", "web", "images", "stack", "infra"):
        assert f"  {job}:" in text
    assert "infra/production/Dockerfile.web" in text
    assert "infra/production/compose.yaml" in text
    assert "terraform fmt -check -recursive" in text


def test_terraform_pr_validation_is_offline_and_manual_cloud_is_wif() -> None:
    text = TERRAFORM.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "terraform init -backend=false" in text
    assert "id-token: write" in text
    assert "google-github-actions/auth@" in text
    assert "workload_identity_provider:" in text
    assert "service_account:" in text
    assert "bootstrap.tfplan" not in text
    assert "production.tfplan" in text


def test_terraform_apply_uses_exact_plan_and_production_gate() -> None:
    text = TERRAFORM.read_text(encoding="utf-8")
    assert "environment: production" in text
    assert "plan_run_id" in text
    assert "sha256sum --check" in text
    assert "terraform apply -input=false production.tfplan" in text
    assert "apply-production" in text


def test_deploy_is_manual_locked_wif_iap_and_vm_reads_secrets() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "environment: production" in text
    assert "concurrency:" in text and "production-deployment" in text
    assert "id-token: write" in text
    assert "git merge-base --is-ancestor" in text
    assert "validate_manifest.py" in text
    assert "--tunnel-through-iap" in text
    assert "gcloud compute scp" in text
    assert "gcloud compute ssh" in text
    assert "scripts/deploy/deploy.sh" in text
    assert "secrets versions access" not in text
    assert "runtime.env" not in text


def test_cloud_workflows_have_no_key_or_unsafe_event() -> None:
    combined = TERRAFORM.read_text(encoding="utf-8") + DEPLOY.read_text(encoding="utf-8")
    assert "pull_request_target" not in combined
    assert "credentials_json" not in combined
    assert "service_account_key" not in combined
    assert "ssh-private-key" not in combined
    assert "BEGIN PRIVATE" not in combined
    for line in combined.splitlines():
        if "uses:" in line:
            reference = line.split("@", 1)[-1].split()[0]
            assert len(reference) == 40 and all(char in "0123456789abcdef" for char in reference)
