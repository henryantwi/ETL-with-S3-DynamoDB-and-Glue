output "table_arn" {
  description = "ARN of the DynamoDB table"
  value       = aws_dynamodb_table.this.arn
}

output "table_name" {
  description = "Name of the DynamoDB table"
  value       = aws_dynamodb_table.this.name
}

output "date_index_arn" {
  description = "ARN of the date-index GSI (PK=date, SK=genre)"
  value       = "${aws_dynamodb_table.this.arn}/index/date-index"
}
