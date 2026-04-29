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
  project             = var.project_id
  name                = var.service_name
  location            = var.region
  deletion_protection = false

  template {
    service_account = google_service_account.service.email

    scaling {
      max_instance_count = var.max_instances
    }

    # GCS FUSE volume — always mounted, scoped to data/ prefix
    dynamic "volumes" {
      for_each = var.data_bucket != "" ? [1] : []
      content {
        name = "solution-data"
        gcs {
          bucket        = var.data_bucket
          read_only     = false
          mount_options = ["only-dir=data"]
        }
      }
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

      # Data volume mount
      dynamic "volume_mounts" {
        for_each = var.data_bucket != "" ? [1] : []
        content {
          name       = "solution-data"
          mount_path = "/mnt/data"
        }
      }

      # Plain env vars
      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      # Secret-backed env vars
      dynamic "env" {
        for_each = var.secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }
}

# Grant service account access to each declared secret
resource "google_secret_manager_secret_iam_member" "declared_secret" {
  for_each  = toset(values(var.secrets))
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.service.email}"
}

# Grant service account access to data bucket (always when bucket provided)
resource "google_storage_bucket_iam_member" "data_bucket" {
  count  = var.data_bucket != "" ? 1 : 0
  bucket = var.data_bucket
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.service.email}"
}

# Public access — explicit flag
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.public ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.service.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
