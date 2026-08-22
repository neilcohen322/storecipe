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


def test_network_is_web_public_and_iap_ssh_only() -> None:
    text = combined()
    assert '["80", "443"]' in text
    assert '["35.235.240.0/20"]' in text
    assert 'ports    = ["22"]' in text
    assert "default" not in (PRODUCTION / "network.tf").read_text(encoding="utf-8").lower()


def test_private_storage_and_empty_secret() -> None:
    text = combined()
    assert text.count("uniform_bucket_level_access = true") == 2
    assert text.count('public_access_prevention    = "enforced"') == 2
    assert "retention_duration_seconds = 604800" in text
    assert "google_secret_manager_secret_version" not in text
    assert "secret_data" not in text
    assert "google_service_account_key" not in text


def test_budget_has_three_actual_spend_thresholds() -> None:
    text = (PRODUCTION / "budget.tf").read_text(encoding="utf-8")
    for threshold in ("0.5", "0.8", "1.0"):
        assert f"threshold_percent = {threshold}" in text
    assert text.count('spend_basis       = "CURRENT_SPEND"') == 3
