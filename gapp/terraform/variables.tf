variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "service_name" {
  description = "Solution name used as Cloud Run service name"
  type        = string
}

variable "image" {
  description = "Container image URL"
  type        = string
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

variable "max_instances" {
  description = "Maximum number of instances"
  type        = number
  default     = 1
}

variable "env" {
  description = "Environment variables"
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Map of env var name to Secret Manager secret ID"
  type        = map(string)
  default     = {}
}

variable "public" {
  description = "Allow unauthenticated access"
  type        = bool
  default     = false
}

variable "data_bucket" {
  description = "The deployment's data bucket, mounted in the container"
  type        = string
}

# Required, like data_bucket: a deploy that stopped saying whether the
# bucket exists would try to create one that is already there.
variable "adopt_data_bucket" {
  description = "The data bucket exists in the project already, so take it into state instead of creating it"
  type        = bool
}

variable "custom_domain" {
  description = "Custom domain to map to the Cloud Run service (e.g., mcp.example.com)"
  type        = string
  default     = ""
}

# Required: the deployment labels gapp writes on every service
# (gapp-deployment and gapp-version). The
# version guard and `gapp list` read them, so no default: a deploy that
# stopped emitting them would fail plan instead of shipping an unlabeled
# service.
variable "labels" {
  description = "Deployment labels for the Cloud Run service"
  type        = map(string)
}
