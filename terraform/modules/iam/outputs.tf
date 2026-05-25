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
