locals {
  repository = "${var.github_owner}/${var.github_repository}"
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "storecipe-runtime"
  display_name = "Storecipe production runtime"
}

resource "google_service_account" "deploy" {
  project      = var.project_id
  account_id   = "storecipe-deploy"
  display_name = "Storecipe production deployment"
}

resource "google_secret_manager_secret_iam_member" "runtime" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.runtime.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_media" {
  bucket = google_storage_bucket.media.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_backup" {
  bucket = google_storage_bucket.backup.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_service_account_iam_member" "deploy_wif" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${var.workload_identity_pool_name}/attribute.environment/production"
}

resource "google_project_iam_member" "deploy_iap" {
  project = var.project_id
  role    = "roles/iap.tunnelResourceAccessor"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_member" "deploy_os_login" {
  project = var.project_id
  role    = "roles/compute.osAdminLogin"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_member" "deploy_viewer" {
  project = var.project_id
  role    = "roles/compute.viewer"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_service_account_iam_member" "deploy_runtime_user" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}

# Compute refuses to attach a service account unless the caller has actAs on it.
# serviceAccountAdmin does not include that permission.
resource "google_service_account_iam_member" "terraform_runtime_user" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:storecipe-terraform@${var.project_id}.iam.gserviceaccount.com"
}
