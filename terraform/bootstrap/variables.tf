variable "aws_region" {
  type        = string
  description = "AWS region"
  default     = "eu-west-1"
}

variable "github_repo" {
  type        = string
  description = "GitHub repo slug (owner/name) allowed to assume the CI roles"
  default     = "henryantwi/Project-1"
}

variable "state_bucket" {
  type        = string
  description = "Terraform remote-state S3 bucket the CI roles must access"
  default     = "terraform-state-559050223770"
}

variable "lock_table" {
  type        = string
  description = "DynamoDB table used for Terraform state locking"
  default     = "terraform-locks"
}

variable "deploy_environment" {
  type        = string
  description = "GitHub Environment that gates deploys (used in the deploy role trust sub)"
  default     = "production"
}
