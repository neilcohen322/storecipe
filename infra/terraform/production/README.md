# Storecipe production Terraform

This root creates the bounded single-VM production platform. Copy `backend.tf.example`
to the ignored `backend.tf` and replace its bucket with the verified bootstrap output.
Copy `terraform.tfvars.example` to the ignored `terraform.tfvars`, and keep secret
payloads out of every input. The billing account ID is used only for the alerting
budget. Set `budget_notification_email` to an operator-controlled address and verify
that it receives notifications; a budget does not cap charges.

Run `terraform fmt -check`, `terraform init`, `terraform validate`, then save and inspect
an exact plan. `machine_type` accepts only `e2-micro` or `e2-small`. The VM has a 10 GB
boot disk and a separate 20 GB `pd-standard` data disk protected by
`prevent_destroy`. A machine-type update intentionally stops and restarts the VM while
the separate attachment remains managed. Only TCP 80/443 is public; TCP 22 accepts
only the IAP range.

Terraform creates the empty `storecipe-production-env` Secret Manager container. Add a
version only in the operator step after DNS/Auth0 values exist; never pass secret data to
Terraform. Applying infrastructure is intentionally outside the agent-built phase.
