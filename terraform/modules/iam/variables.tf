variable "raw_bucket_arn" {
  type        = string
  description = "ARN of raw-data bucket"
}

variable "archive_bucket_arn" {
  type        = string
  description = "ARN of archive bucket"
}

variable "glue_scripts_bucket_arn" {
  type        = string
  description = "ARN of glue-scripts bucket"
}

variable "aws_region" {
  type        = string
  description = "AWS region"
}

variable "aws_account_id" {
  type        = string
  description = "AWS account ID"
}
