output "state_bucket" {
  value = google_storage_bucket.terraform_state.name
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "workload_identity_pool_name" {
  value = google_iam_workload_identity_pool.github.name
}

output "terraform_service_account" {
  value = google_service_account.terraform.email
}
