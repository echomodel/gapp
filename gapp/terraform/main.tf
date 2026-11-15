terraform {
  required_version = ">= 1.7"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
  backend "gcs" {}
}

module "service" {
  source        = "./modules/cloud-run-service"
  project_id    = var.project_id
  service_name  = var.service_name
  image         = var.image
  memory        = var.memory
  cpu           = var.cpu
  max_instances = var.max_instances
  env           = var.env
  secrets       = var.secrets
  data_bucket   = var.data_bucket
  public        = var.public
  labels        = var.labels
}

# A data bucket that exists before Terraform has it in state is taken
# over as it is, never created over or replaced. That is the case after a
# deploy that made the bucket and then failed, and for a deployment
# whose state was lost. gapp sets the flag when it finds the bucket in
# the project; for a bucket already in state the block does nothing.
import {
  for_each = var.adopt_data_bucket ? toset([var.data_bucket]) : toset([])
  to       = module.service.google_storage_bucket.data
  id       = "${var.project_id}/${each.value}"
}

resource "google_cloud_run_domain_mapping" "custom" {
  count    = var.custom_domain != "" ? 1 : 0
  project  = var.project_id
  location = "us-central1"
  name     = var.custom_domain

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = module.service.service_name
  }
}

output "service_url" {
  value = module.service.service_url
}

output "custom_domain" {
  value = var.custom_domain
}
