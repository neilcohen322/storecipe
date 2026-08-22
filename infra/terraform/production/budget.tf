data "google_project" "current" {
  project_id = var.project_id
}

resource "google_monitoring_notification_channel" "budget_email" {
  project      = var.project_id
  display_name = "Storecipe production budget email"
  type         = "email"
  labels = {
    email_address = var.budget_notification_email
  }

  depends_on = [google_project_service.production]
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

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.budget_email.name]
    disable_default_iam_recipients   = false
  }

  depends_on = [google_project_service.production]
}
