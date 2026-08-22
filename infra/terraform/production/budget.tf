data "google_project" "current" {
  project_id = var.project_id
}

resource "google_billing_budget" "production" {
  billing_account = var.billing_account_id
  display_name    = "Storecipe Production Monthly"

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_amount_usd)
    }
  }

  budget_filter {
    calendar_period = "MONTH"
    projects        = ["projects/${data.google_project.current.number}"]
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 0.8
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  depends_on = [google_project_service.production]
}
