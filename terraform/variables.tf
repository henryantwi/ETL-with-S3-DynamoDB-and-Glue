variable "project_name" {
  type        = string
  description = "Project identifier used in resource names"
  default     = "etl"
}

variable "environment" {
  type        = string
  description = "Deployment environment (dev, staging, prod)"
  default     = "dev"
}

variable "bucket_suffix" {
  type        = string
  description = "Unique suffix for global S3 bucket name uniqueness"
}

variable "aws_region" {
  type        = string
  description = "AWS region for all resources"
  default     = "eu-west-1"
}

variable "aws_account_id" {
  type        = string
  description = "AWS account ID for ARN construction"
}
