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

output "glue_validation_job_name" {
  description = "Glue validation job name (for Step Functions state machine reference)"
  value       = module.glue_validate.job_name
}

output "raw_bucket_id" {
  description = "Name of raw-data bucket"
  value       = module.raw_data.bucket_id
}

output "glue_scripts_bucket_id" {
  description = "Name of glue-scripts bucket"
  value       = module.glue_scripts.bucket_id
}

output "processed_bucket_id" {
  description = "Name of processed-data bucket"
  value       = module.processed_data.bucket_id
}

output "processed_bucket_arn" {
  description = "ARN of processed-data bucket"
  value       = module.processed_data.bucket_arn
}

output "music_kpis_table_arn" {
  description = "ARN of MusicKPIs DynamoDB table"
  value       = module.dynamodb.table_arn
}

output "music_kpis_table_name" {
  description = "Name of MusicKPIs DynamoDB table"
  value       = module.dynamodb.table_name
}

output "etl_pipeline_state_machine_arn" {
  description = "ARN of the etl-pipeline Step Functions state machine"
  value       = aws_sfn_state_machine.etl_pipeline.arn
}

output "etl_pipeline_state_machine_name" {
  description = "Name of the etl-pipeline Step Functions state machine"
  value       = aws_sfn_state_machine.etl_pipeline.name
}

output "glue_archive_job_name" {
  description = "Glue archive job name"
  value       = module.glue_archive_files.job_name
}

output "pipeline_dispatch_queue_url" {
  description = "URL of the pipeline dispatch SQS queue"
  value       = aws_sqs_queue.pipeline_dispatch.id
}

output "pipeline_dispatch_queue_arn" {
  description = "ARN of the pipeline dispatch SQS queue"
  value       = aws_sqs_queue.pipeline_dispatch.arn
}

output "pipeline_dispatch_dlq_arn" {
  description = "ARN of the pipeline dispatch dead-letter queue"
  value       = aws_sqs_queue.pipeline_dispatch_dlq.arn
}

output "pipeline_dispatcher_function_name" {
  description = "Name of the pipeline dispatcher Lambda"
  value       = aws_lambda_function.pipeline_dispatcher.function_name
}
