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
  env           = var.env
  secrets       = var.secrets
  data_bucket   = var.data_bucket
  public        = var.public
  auth_enabled  = var.auth_enabled
}

output "service_url" {
  value = module.service.service_url
}
