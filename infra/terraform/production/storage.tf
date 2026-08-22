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
