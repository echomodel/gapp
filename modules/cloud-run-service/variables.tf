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

variable "auth_enabled" {
  description = "Enable credential mediation wrapper"
  type        = bool
  default     = false
}

variable "auth_bucket" {
  description = "GCS bucket for credential files (used for FUSE mount)"
  type        = string
  default     = ""
}
