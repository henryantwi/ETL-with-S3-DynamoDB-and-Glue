terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Bucket name must be set per-account. Replace via -backend-config or edit
  # locally before `terraform init`. Format: terraform-state-{account_id}.
  backend "s3" {
    bucket         = "terraform-state-REPLACE_WITH_ACCOUNT_ID"
    key            = "001-secure-storage-foundation/terraform.tfstate"
    region         = "ap-southeast-2"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
}
