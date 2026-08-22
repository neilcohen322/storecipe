variable "project_id" {
  type        = string
  description = "Dedicated Storecipe production GCP project."
}

variable "billing_account_id" {
  type        = string
  description = "Billing account for the alert-only budget."
  sensitive   = true
}

variable "region" {
  type    = string
  default = "us-central1"
  validation {
    condition     = contains(["us-central1", "us-east1", "us-west1"], var.region)
    error_message = "Use an eligible US Free Tier region: us-central1, us-east1, or us-west1."
  }
}

variable "zone" {
  type    = string
  default = "us-central1-a"
  validation {
    condition     = startswith(var.zone, "${var.region}-")
    error_message = "zone must belong to region."
  }
}

variable "machine_type" {
  type    = string
  default = "e2-micro"
  validation {
    condition     = contains(["e2-micro", "e2-small"], var.machine_type)
    error_message = "machine_type must be e2-micro or e2-small."
  }
}

variable "bucket_suffix" {
  type        = string
  description = "Globally unique lowercase suffix for media and backup buckets."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,20}$", var.bucket_suffix))
    error_message = "Use 3-21 lowercase letters, digits, or hyphens."
  }
}

variable "workload_identity_pool_name" {
  type        = string
  description = "Full bootstrap WIF pool name (projects/NUMBER/locations/global/workloadIdentityPools/NAME)."
}

variable "github_owner" {
  type    = string
  default = "neilcohen322"
}

variable "github_repository" {
  type    = string
  default = "storecipe"
}

variable "budget_amount_usd" {
  type    = number
  default = 10
  validation {
    condition     = var.budget_amount_usd >= 1
    error_message = "budget_amount_usd must be at least 1."
  }
}

variable "deletion_protection" {
  type        = bool
  default     = true
  description = "Protect the production VM from accidental Terraform deletion."
}
