resource "google_monitoring_uptime_check_config" "ready" {
  project      = var.project_id
  display_name = "Storecipe production readiness"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/health/ready"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.public_hostname
    }
  }

  depends_on = [google_project_service.production]
}

resource "google_monitoring_alert_policy" "uptime_ready" {
  project      = var.project_id
  display_name = "Storecipe production uptime/readiness"
  combiner     = "OR"
  notification_channels = [
    google_monitoring_notification_channel.budget_email.name
  ]

  conditions {
    display_name = "Readiness uptime check failed"
    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "60s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
      trigger {
        count = 1
      }
    }
  }

  depends_on = [google_project_service.production]
}

resource "google_monitoring_alert_policy" "capacity_cpu" {
  project      = var.project_id
  display_name = "Storecipe production capacity"
  combiner     = "OR"
  notification_channels = [
    google_monitoring_notification_channel.budget_email.name
  ]

  conditions {
    display_name = "CPU utilization above 80 percent for 15 minutes"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"gce_instance\"",
        "metric.type = \"compute.googleapis.com/instance/cpu/utilization\"",
        "resource.labels.instance_id = \"${google_compute_instance.production.instance_id}\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "900s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  conditions {
    display_name = "RSS above 80 percent for 15 minutes"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"gce_instance\"",
        "metric.type = \"agent.googleapis.com/memory/percent_used\"",
        "metric.labels.state = \"used\"",
        "resource.labels.instance_id = \"${google_compute_instance.production.instance_id}\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 80
      duration        = "900s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  conditions {
    display_name = "Free memory below 150 MiB for 15 minutes"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"gce_instance\"",
        "metric.type = \"agent.googleapis.com/memory/bytes_used\"",
        "metric.labels.state = \"free\"",
        "resource.labels.instance_id = \"${google_compute_instance.production.instance_id}\"",
      ])
      comparison      = "COMPARISON_LT"
      threshold_value = 157286400
      duration        = "900s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  conditions {
    display_name = "Swap use above 512 MiB for 15 minutes"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"gce_instance\"",
        "metric.type = \"agent.googleapis.com/swap/bytes_used\"",
        "metric.labels.state = \"used\"",
        "resource.labels.instance_id = \"${google_compute_instance.production.instance_id}\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 536870912
      duration        = "900s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  depends_on = [google_project_service.production]
}

resource "google_logging_metric" "incomplete_account_deletion" {
  project     = var.project_id
  name        = "storecipe_incomplete_account_deletion"
  description = "Account deletion jobs still incomplete after 15 minutes."
  filter      = "textPayload:\"account_deletion.incomplete\" OR jsonPayload.message:\"account_deletion.incomplete\" OR jsonPayload.log:\"account_deletion.incomplete\""

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }

  depends_on = [google_project_service.production]
}

resource "google_monitoring_alert_policy" "incomplete_account_deletion" {
  project      = var.project_id
  display_name = "Storecipe incomplete account deletion"
  combiner     = "OR"
  notification_channels = [
    google_monitoring_notification_channel.budget_email.name
  ]

  conditions {
    display_name = "Incomplete deletion after 15 minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/storecipe_incomplete_account_deletion\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_DELTA"
      }
      trigger {
        count = 1
      }
    }
  }

  depends_on = [google_logging_metric.incomplete_account_deletion]
}
