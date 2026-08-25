resource "google_storage_bucket" "media" {
  name                        = "${var.project_id}-media-${var.bucket_suffix}"
  project                     = var.project_id
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = false
  }

  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  depends_on = [google_project_service.production]
}

resource "google_storage_bucket" "backup" {
  name                        = "${var.project_id}-backup-${var.bucket_suffix}"
  project                     = var.project_id
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 45
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.production]
}

# This journal is intentionally separate from media, backups, and Terraform state.
# The retention policy protects a newly written deletion record long enough to replay
# an interrupted account-deletion workflow. Runtime deletes only completed triples
# after saga-completion plus 90 days; pending records are never age-deleted.
resource "google_storage_bucket" "account_deletion_journal" {
  name                        = "${var.project_id}-account-deletion-journal-${var.bucket_suffix}"
  project                     = var.project_id
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    # Old journal generations must not extend the lifetime of erased accounts.
    enabled = false
  }

  # GCS otherwise applies a default soft-delete window. Erasure correctness does
  # not rely on it; the explicit retention and lifecycle rules below govern records.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  retention_policy {
    retention_period = 7776000 # 90 days: completed records cannot be deleted earlier
  }

  # Do not age-delete journal objects. Pending deletions must remain recoverable
  # after 90 days. Completed .json/.committed/.completed triples are deleted by
  # Catalog after completedAt plus 90 days, once bucket retention allows it.

  depends_on = [google_project_service.production]
}
