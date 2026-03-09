terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

# Service account for the Cloud Run service
resource "google_service_account" "service" {
  project      = var.project_id
  account_id   = "${var.service_name}-sa"
  display_name = "${var.service_name} service account"
}

# Cloud Run v2 service
resource "google_cloud_run_v2_service" "service" {
  project  = var.project_id
  name     = var.service_name
  location = var.region

  template {
    service_account = google_service_account.service.email

    scaling {
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
      }

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
}

# Public access (if enabled)
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.public ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.service.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
