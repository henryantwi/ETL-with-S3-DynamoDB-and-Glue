output "job_name" {
  description = "Glue job name (for Step Functions state machine reference)"
  value       = aws_glue_job.this.name
}
