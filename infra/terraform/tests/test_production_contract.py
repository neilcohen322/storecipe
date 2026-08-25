import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
PRODUCTION = ROOT / "infra" / "terraform" / "production"


def combined() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in PRODUCTION.glob("*.tf"))


def test_machine_and_disk_contract() -> None:
    text = combined()
    assert '["e2-micro", "e2-small"]' in text
    assert 'default = "e2-micro"' in text
    assert "size  = 10" in text
    assert "size                      = 20" in text
    assert re.search(r'device_name\s*=\s*"storecipe-data"', text)
    assert "auto_delete = true" in text  # boot disk only
    assert re.search(r'deletion_policy\s*=\s*"KEEP"', text)
    assert "google_compute_attached_disk" in text
    assert "google_compute_disk.data" in text
    assert "allow_stopping_for_update = true" in text
    assert "ignore_changes = [attached_disk]" in text
    assert "prevent_destroy = true" in text


def test_network_is_web_public_and_iap_ssh_only() -> None:
    text = combined()
    assert '["80", "443"]' in text
    assert '["35.235.240.0/20"]' in text
    assert 'ports    = ["22"]' in text
    assert "default" not in (PRODUCTION / "network.tf").read_text(encoding="utf-8").lower()


def test_private_storage_and_empty_secret() -> None:
    text = combined()
    assert text.count("uniform_bucket_level_access = true") == 3
    assert text.count('public_access_prevention    = "enforced"') == 3
    assert "retention_duration_seconds = 604800" in text
    assert "google_secret_manager_secret_version" not in text
    assert "secret_data" not in text
    assert "google_service_account_key" not in text


def test_account_deletion_journal_storage_contract() -> None:
    storage = (PRODUCTION / "storage.tf").read_text(encoding="utf-8")
    assert 'resource "google_storage_bucket" "account_deletion_journal"' in storage
    assert (
        'name                        = "${var.project_id}-account-deletion-journal-'
        '${var.bucket_suffix}"'
    ) in storage
    assert "uniform_bucket_level_access = true" in storage
    assert 'public_access_prevention    = "enforced"' in storage
    assert re.search(r"retention_period = 7776000", storage)
    journal_start = storage.index('resource "google_storage_bucket" "account_deletion_journal"')
    journal = storage[journal_start:]
    assert "lifecycle_rule" not in journal
    assert re.search(
        r"versioning\s*\{\s*# Old journal generations.*?enabled = false", storage, re.DOTALL
    )
    assert re.search(r"soft_delete_policy\s*\{\s*retention_duration_seconds = 0", storage)


def test_account_deletion_journal_runtime_iam_and_binding_contract() -> None:
    iam = (PRODUCTION / "iam.tf").read_text(encoding="utf-8")
    compute = (PRODUCTION / "compute.tf").read_text(encoding="utf-8")
    startup = (PRODUCTION / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    outputs = (PRODUCTION / "outputs.tf").read_text(encoding="utf-8")

    assert 'resource "google_project_iam_custom_role" "account_deletion_journal_runtime"' in iam
    for permission in (
        "storage.objects.create",
        "storage.objects.get",
        "storage.objects.list",
        "storage.objects.delete",
    ):
        assert f'"{permission}"' in iam
    assert 'resource "google_storage_bucket_iam_member" "runtime_account_deletion_journal"' in iam
    assert "bucket = google_storage_bucket.account_deletion_journal.name" in iam
    assert "role   = google_project_iam_custom_role.account_deletion_journal_runtime.name" in iam
    assert 'member = "serviceAccount:${google_service_account.runtime.email}"' in iam
    assert "allUsers" not in iam
    assert "allAuthenticatedUsers" not in iam
    assert (
        "account_deletion_journal_bucket_name = google_storage_bucket.account_deletion_journal.name"
        in compute
    )
    assert (
        "CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET=${account_deletion_journal_bucket_name}" in startup
    )
    assert 'output "account_deletion_journal_bucket"' in outputs


def test_budget_has_three_actual_spend_thresholds() -> None:
    text = (PRODUCTION / "budget.tf").read_text(encoding="utf-8")
    for threshold in ("0.5", "0.8", "1.0"):
        assert f"threshold_percent = {threshold}" in text
    assert text.count('spend_basis       = "CURRENT_SPEND"') == 3
    assert "all_updates_rule" in text
    assert "google_monitoring_notification_channel.budget_email.name" in text
    assert re.search(r"disable_default_iam_recipients\s*=\s*false", text)


def test_terraform_identity_can_attach_runtime_service_account() -> None:
    iam = (PRODUCTION / "iam.tf").read_text(encoding="utf-8")
    compute = (PRODUCTION / "compute.tf").read_text(encoding="utf-8")
    assert 'resource "google_service_account_iam_member" "terraform_runtime_user"' in iam
    assert 'role               = "roles/iam.serviceAccountUser"' in iam
    assert (
        'member             = "serviceAccount:storecipe-terraform@${var.project_id}'
        '.iam.gserviceaccount.com"'
    ) in iam
    assert "google_service_account_iam_member.terraform_runtime_user" in compute


def test_production_has_uptime_capacity_and_incomplete_deletion_alerts() -> None:
    text = combined()
    assert 'resource "google_monitoring_uptime_check_config" "ready"' in text
    assert 'path         = "/health/ready"' in text
    assert 'resource "google_monitoring_alert_policy" "uptime_ready"' in text
    assert 'resource "google_monitoring_alert_policy" "capacity_cpu"' in text
    assert 'duration        = "900s"' in text
    assert "agent.googleapis.com/memory/percent_used" in text
    assert "157286400" in text
    assert "536870912" in text
    assert "agent.googleapis.com/swap/bytes_used" in text
    swap_at = text.index("agent.googleapis.com/swap/bytes_used")
    assert 'metric.labels.state = \\"used\\"' in text[swap_at : swap_at + 250]
    assert 'resource "google_logging_metric" "incomplete_account_deletion"' in text
    assert "account_deletion.incomplete" in text
    assert "jsonPayload.log" in text
    assert 'resource "google_monitoring_alert_policy" "incomplete_account_deletion"' in text
    assert "google_monitoring_notification_channel.budget_email.name" in text
    assert "logging.googleapis.com" in text
    assert 'variable "public_hostname"' in text
    assert "roles/logging.logWriter" in text
    assert "roles/monitoring.metricWriter" in text
    startup = (PRODUCTION / "templates" / "startup.sh.tftpl").read_text(encoding="utf-8")
    assert "google-cloud-ops-agent" in startup
    assert "/var/lib/storecipe/docker/containers" in startup


def test_dependabot_and_ci_audit_gates() -> None:
    root = Path(__file__).parents[3]
    dependabot = (root / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "package-ecosystem: pip" in dependabot
    assert "package-ecosystem: npm" in dependabot
    assert "uv run pip-audit" in ci
    assert "pnpm audit --prod --audit-level=high" in ci
