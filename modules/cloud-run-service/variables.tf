variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
}

variable "image" {
  description = "Container image URL"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "env" {
  description = "Environment variables"
  type        = map(string)
  default     = {}
}

variable "max_instances" {
  description = "Maximum number of instances"
  type        = number
  default     = 1
}

variable "memory" {
  description = "Memory limit"
  type        = string
  default     = "512Mi"
}

variable "cpu" {
  description = "CPU limit"
  type        = string
  default     = "1"
}

variable "secrets" {
  description = "Map of env var name to Secret Manager secret ID"
  type        = map(string)
  default     = {}
}

variable "public" {
  description = "Allow unauthenticated access (allUsers). Default false — Cloud Run IAM blocks traffic."
  type        = bool
  default     = false
}

variable "data_bucket" {
  description = "GCS bucket for solution data (FUSE mounted at /mnt/data, scoped to data/ prefix)"
  type        = string
  default     = ""
}

variable "auth_enabled" {
  description = "Enable credential mediation wrapper"
  type        = bool
  default     = false
}
