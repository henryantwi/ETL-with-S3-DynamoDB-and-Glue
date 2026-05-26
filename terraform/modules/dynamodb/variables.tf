variable "table_name" {
  type        = string
  description = "DynamoDB table name"
}

variable "project_name" {
  type        = string
  description = "Project name tag"
}

variable "environment" {
  type        = string
  description = "Deployment environment (e.g. dev, prod)"
}
