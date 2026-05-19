output "raw_bucket_arn" {
  description = "ARN of raw-data bucket"
  value       = module.raw_data.bucket_arn
}

output "archive_bucket_arn" {
  description = "ARN of archive bucket"
  value       = module.archive.bucket_arn
}

output "glue_scripts_bucket_arn" {
  description = "ARN of glue-scripts bucket"
  value       = module.glue_scripts.bucket_arn
}

output "glue_validation_role_arn" {
  description = "ARN of etl-glue-validation-role"
  value       = module.iam.glue_validation_role_arn
}

output "stepfunctions_role_arn" {
  description = "ARN of etl-stepfunctions-role"
  value       = module.iam.stepfunctions_role_arn
}
