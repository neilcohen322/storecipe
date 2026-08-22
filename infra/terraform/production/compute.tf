resource "google_compute_disk" "data" {
  name                      = "storecipe-production-data"
  project                   = var.project_id
  zone                      = var.zone
  type                      = "pd-standard"
  size                      = 20
  physical_block_size_bytes = 4096
}

resource "google_compute_instance" "production" {
  name                = "storecipe-production"
  project             = var.project_id
  zone                = var.zone
  machine_type        = var.machine_type
  deletion_protection = var.deletion_protection
  tags                = ["storecipe-web", "storecipe-iap"]

  boot_disk {
    auto_delete = true
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
      type  = "pd-standard"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.production.id
    access_config {
      nat_ip       = google_compute_address.production.address
      network_tier = "PREMIUM"
    }
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
  }
  metadata_startup_script = templatefile("${path.module}/templates/startup.sh.tftpl", {
    runtime_secret_name = google_secret_manager_secret.runtime.secret_id
    media_bucket_name   = google_storage_bucket.media.name
    backup_bucket_name  = google_storage_bucket.backup.name
  })

  service_account {
    email  = google_service_account.runtime.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
  }

  lifecycle {
    precondition {
      condition     = google_compute_disk.data.size == 20
      error_message = "The persistent data disk must remain 20 GB."
    }
  }

  depends_on = [google_project_service.production]
}

resource "google_compute_attached_disk" "data" {
  project         = var.project_id
  zone            = var.zone
  disk            = google_compute_disk.data.id
  instance        = google_compute_instance.production.id
  device_name     = "storecipe-data"
  mode            = "READ_WRITE"
  deletion_policy = "KEEP"
}
