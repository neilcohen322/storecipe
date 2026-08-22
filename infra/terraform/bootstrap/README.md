# Terraform bootstrap

This root creates only the private versioned state bucket, GitHub Workload Identity
Federation, and the Terraform service account. It does not create a VM, application
bucket, database, service-account key, or secret payload.

1. Copy `terraform.tfvars.example` to the ignored `terraform.tfvars` and enter only the
   project, billing account, region, GitHub repository, and unique bucket suffix.
2. Run `terraform init`, `terraform fmt -check`, `terraform validate`,
   `terraform plan -out bootstrap.tfplan`, and inspect `terraform show bootstrap.tfplan`.
3. Apply only the inspected file with `terraform apply bootstrap.tfplan`.
4. Copy `backend.tf.example` to the ignored `backend.tf` and replace
   `REPLACE_WITH_BOOTSTRAP_STATE_BUCKET` with the `state_bucket` output. The result is:

   ```hcl
   terraform {
     backend "gcs" {
       bucket = "OUTPUT_STATE_BUCKET"
       prefix = "bootstrap"
     }
   }
   ```

5. Run `terraform init -migrate-state`, verify an object version exists in the private
   bucket, then run `terraform plan` and require no unexpected changes.

Do not remove local state or a plan until remote state is verified. Afterwards, only the
ignored local `*.tfstate*`, `*.tfplan`, and `terraform.tfvars` files may be securely
removed. Never commit them or send their contents to an agent.
