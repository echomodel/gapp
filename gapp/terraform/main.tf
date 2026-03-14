terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
  backend "gcs" {}
}

module "service" {
  source        = "github.com/krisrowe/gapp//modules/cloud-run-service"
  project_id    = var.project_id
  service_name  = var.service_name
  image         = var.image
  memory        = var.memory
  cpu           = var.cpu
  max_instances = var.max_instances
  public        = var.public
  env           = var.env
  secrets       = var.secrets
  auth_enabled  = var.auth_enabled
  auth_bucket   = var.auth_bucket
}

output "service_url" {
  value = module.service.service_url
}
