terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # Bucket name must be set per-account. Replace via -backend-config or edit
  # locally before `terraform init`. Format: terraform-state-{account_id}.
  backend "s3" {
    bucket         = "terraform-state-REPLACE_WITH_ACCOUNT_ID"
    key            = "001-secure-storage-foundation/terraform.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
}
