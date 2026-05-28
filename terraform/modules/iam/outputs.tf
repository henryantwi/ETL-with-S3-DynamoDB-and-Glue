output "glue_validation_role_arn" {
  description = "ARN of etl-glue-validation-role"
  value       = aws_iam_role.glue_validation.arn
}

output "stepfunctions_role_arn" {
  description = "ARN of etl-stepfunctions-role"
  value       = aws_iam_role.stepfunctions.arn
}

output "glue_transform_role_arn" {
  description = "ARN of etl-glue-transform-role"
  value       = aws_iam_role.glue_transform.arn
}

output "glue_writer_role_arn" {
  description = "ARN of etl-glue-writer-role"
  value       = aws_iam_role.glue_writer.arn
}

output "glue_archive_role_arn" {
  description = "ARN of etl-glue-archive-role"
  value       = aws_iam_role.glue_archive.arn
}

output "metrics_reader_policy_arn" {
  description = "ARN of metrics-reader-policy (attach to consumer IAM roles)"
  value       = aws_iam_policy.metrics_reader.arn
}
