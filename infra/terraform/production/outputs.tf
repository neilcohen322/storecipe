output "static_ip" {
  value = google_compute_address.production.address
}

output "vm_name" {
  value = google_compute_instance.production.name
}

output "zone" {
  value = var.zone
}

output "media_bucket" {
  value = google_storage_bucket.media.name
}

output "backup_bucket" {
  value = google_storage_bucket.backup.name
}

output "runtime_secret_name" {
  value = google_secret_manager_secret.runtime.secret_id
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "deploy_service_account" {
  value = google_service_account.deploy.email
}
