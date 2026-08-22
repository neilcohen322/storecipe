variable "project_id" {
  description = "Billing-enabled GCP project ID."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account used only for budget-management IAM."
  type        = string
  sensitive   = true
}

variable "region" {
  description = "Region for the Terraform state bucket."
  type        = string
  default     = "us-central1"
}

variable "state_bucket_suffix" {
  description = "Short globally-unique lowercase suffix for the state bucket."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,20}$", var.state_bucket_suffix))
    error_message = "Use 3-21 lowercase letters, digits, or hyphens."
  }
}

variable "github_owner" {
  type    = string
  default = "neilcohen322"
}

variable "github_repository" {
  type    = string
  default = "storecipe"
}
