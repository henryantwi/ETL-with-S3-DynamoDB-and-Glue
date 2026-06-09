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

variable "dispatcher_batching_window_seconds" {
  type        = number
  description = "Max seconds SQS waits to fill a batch before invoking the dispatcher Lambda (coalesces a burst of uploads into one run)"
  default     = 90
}

variable "dispatcher_queue_visibility_timeout_seconds" {
  type        = number
  description = "Visibility timeout for the pipeline dispatch queue; must exceed the max pipeline duration"
  default     = 960
}

variable "dispatcher_max_receive_count" {
  type        = number
  description = "Receives before a dispatch message is dead-lettered; high because deferring during an in-flight run is deliberate polling, not failure"
  default     = 50
}
