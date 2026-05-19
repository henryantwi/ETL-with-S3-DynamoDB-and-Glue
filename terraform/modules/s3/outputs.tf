output "bucket_arn" {
  description = "ARN of created bucket"
  value       = aws_s3_bucket.this.arn
}

output "bucket_id" {
  description = "Bucket name"
  value       = aws_s3_bucket.this.id
}
