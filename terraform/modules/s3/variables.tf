variable "project_name" {
  type        = string
  description = "Project identifier (used in bucket name)"
}

variable "environment" {
  type        = string
  description = "Deployment environment"
}

variable "bucket_suffix" {
  type        = string
  description = "Unique suffix for global bucket uniqueness"
}

variable "bucket_prefix" {
  type        = string
  description = "Bucket name prefix (e.g. raw-data, archive, glue-scripts)"
}

variable "enable_versioning" {
  type        = bool
  description = "Enable S3 versioning"
  default     = false
}

variable "enable_lifecycle" {
  type        = bool
  description = "Enable lifecycle config (Glacier transition + expiration)"
  default     = false
}

variable "lifecycle_transition_days" {
  type        = number
  description = "Days before object transitions to GLACIER"
  default     = 90
}

variable "lifecycle_expiration_days" {
  type        = number
  description = "Days before object is expired/deleted"
  default     = 365
}
