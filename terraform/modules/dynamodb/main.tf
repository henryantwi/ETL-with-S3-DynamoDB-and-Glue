resource "aws_dynamodb_table" "this" {
  name         = var.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "genre"
  range_key    = "date"

  attribute {
    name = "genre"
    type = "S"
  }

  attribute {
    name = "date"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}
