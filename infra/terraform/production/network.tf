resource "google_project_service" "production" {
  for_each = toset([
    "billingbudgets.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "iap.googleapis.com",
    "monitoring.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "production" {
  name                    = "storecipe-production"
  project                 = var.project_id
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  depends_on              = [google_project_service.production]
}

resource "google_compute_subnetwork" "production" {
  name                     = "storecipe-production-${var.region}"
  project                  = var.project_id
  region                   = var.region
  network                  = google_compute_network.production.id
  ip_cidr_range            = "10.24.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_address" "production" {
  name         = "storecipe-production-ip"
  project      = var.project_id
  region       = var.region
  address_type = "EXTERNAL"
  network_tier = "PREMIUM"
}

resource "google_compute_firewall" "web" {
  name          = "storecipe-production-web"
  project       = var.project_id
  network       = google_compute_network.production.name
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["storecipe-web"]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "storecipe-production-iap-ssh"
  project       = var.project_id
  network       = google_compute_network.production.name
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["storecipe-iap"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
