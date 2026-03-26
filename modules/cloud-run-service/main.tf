terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }
}

# Service account for the Cloud Run service
resource "google_service_account" "service" {
  project      = var.project_id
  account_id   = "${var.service_name}-sa"
  display_name = "${var.service_name} service account"
}

# --- Auth: signing key (only when auth enabled) ---

resource "random_password" "signing_key" {
  count   = var.auth_enabled ? 1 : 0
  length  = 32
  special = false
}

resource "google_secret_manager_secret" "signing_key" {
  count     = var.auth_enabled ? 1 : 0
  project   = var.project_id
  secret_id = "${var.service_name}-signing-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "signing_key" {
  count       = var.auth_enabled ? 1 : 0
  secret      = google_secret_manager_secret.signing_key[0].id
  secret_data = random_password.signing_key[0].result
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

      # Auth env vars (only when auth enabled)
      dynamic "env" {
        for_each = var.auth_enabled ? [1] : []
        content {
          name = "GAPP_SIGNING_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.signing_key[0].secret_id
              version = "latest"
            }
          }
        }
      }

      dynamic "env" {
        for_each = var.auth_enabled ? [1] : []
        content {
          name  = "GAPP_AUTH_MOUNT"
          value = "/mnt/data/auth"
        }
      }
    }
  }
}

# Grant service account access to each prerequisite secret
resource "google_secret_manager_secret_iam_member" "prerequisite_secret" {
  for_each  = var.secrets
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.service.email}"
}

# Grant service account access to the signing key secret (only when auth enabled)
resource "google_secret_manager_secret_iam_member" "signing_key" {
  count     = var.auth_enabled ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.signing_key[0].secret_id
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

# Public access — controlled by explicit flag, not tied to auth
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.public ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.service.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
