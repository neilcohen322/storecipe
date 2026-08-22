from pathlib import Path

ROOT = Path(__file__).parents[3]
CI = ROOT / ".github" / "workflows" / "ci.yml"
TERRAFORM = ROOT / ".github" / "workflows" / "terraform.yml"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
BOOTSTRAP = ROOT / "infra" / "terraform" / "bootstrap" / "main.tf"
PRODUCTION_IAM = ROOT / "infra" / "terraform" / "production" / "iam.tf"


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
    assert "GCP_BUDGET_NOTIFICATION_EMAIL" in text


def test_terraform_apply_uses_exact_plan_and_production_gate() -> None:
    text = TERRAFORM.read_text(encoding="utf-8")
    assert "environment: production" in text
    assert "plan_run_id" in text
    assert "sha256sum --check" in text
    assert "terraform apply -input=false production.tfplan" in text
    assert "apply-production" in text
    assert "production-plan-metadata.json" in text
    assert "actions/runs/$PLAN_RUN_ID" in text
    assert '"$(jq -r \'.head_sha\' <<<"$run")" == "$EXPECTED_COMMIT"' in text


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
    assert "project_id: ${{ vars.GCP_PROJECT_ID }}" in text
    assert text.count("CLOUDSDK_CORE_PROJECT: ${{ vars.GCP_PROJECT_ID }}") == 2
    assert '[[ -n "${CLOUDSDK_CORE_PROJECT:-}" ]]' in text
    assert "actions/runs/$RELEASE_RUN_ID" in text
    assert ".github/workflows/release.yml" in text
    assert '--expected-image-prefix "ghcr.io/$owner/storecipe-"' in text


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


def test_shared_wif_provider_preserves_separate_service_account_boundaries() -> None:
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    production_iam = PRODUCTION_IAM.read_text(encoding="utf-8")
    assert ".github/workflows/terraform.yml@refs/heads/master" in bootstrap
    assert ".github/workflows/deploy.yml@refs/heads/master" in bootstrap
    assert "/attribute.workflow/" in bootstrap
    assert "/attribute.repository/${local.repository}" not in bootstrap
    deploy_binding = production_iam.split(
        'resource "google_service_account_iam_member" "deploy_wif"', maxsplit=1
    )[1].split("\n}\n", maxsplit=1)[0]
    assert "/attribute.environment/production" in deploy_binding
