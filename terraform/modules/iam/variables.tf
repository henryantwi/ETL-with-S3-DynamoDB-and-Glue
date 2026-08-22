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

variable "processed_bucket_arn" {
  type        = string
  description = "ARN of processed-data bucket"
}

variable "dynamodb_table_arn" {
  type        = string
  description = "ARN of the MusicKPIs DynamoDB table"
}

variable "dynamodb_date_index_arn" {
  type        = string
  description = "ARN of the MusicKPIs date-index GSI"
}
