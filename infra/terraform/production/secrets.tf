resource "google_secret_manager_secret" "runtime" {
  project   = var.project_id
  secret_id = "storecipe-production-env"
  replication {
    auto {}
  }
  depends_on = [google_project_service.production]
}
