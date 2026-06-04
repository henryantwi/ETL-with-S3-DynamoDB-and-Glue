terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state on purpose: this module bootstraps the OIDC trust that the
  # main stack's remote-state access depends on, so it cannot itself live in
  # that remote state. Apply once with admin credentials; commit the resulting
  # role ARNs to repo secrets. The terraform.tfstate stays local / out of git.
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  account_id       = data.aws_caller_identity.current.account_id
  state_bucket_arn = "arn:aws:s3:::${var.state_bucket}"
  lock_table_arn   = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.lock_table}"
}

# GitHub Actions OIDC identity provider.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

# ---------------------------------------------------------------------------
# Shared: remote-state access (read for plan, read/write for deploy).
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "state_read" {
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [local.state_bucket_arn, "${local.state_bucket_arn}/*"]
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [local.lock_table_arn]
  }
}

data "aws_iam_policy_document" "state_write" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [local.state_bucket_arn, "${local.state_bucket_arn}/*"]
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [local.lock_table_arn]
  }
}

# ---------------------------------------------------------------------------
# Plan role — read-only. Assumed from any repo event (pull_request and push to
# main both run terraform-plan), so the sub is scoped to the repo, not an event.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name               = "Project1-CI-Plan"
  assume_role_policy = data.aws_iam_policy_document.plan_trust.json
}

# Read/describe across the stack's services so `terraform plan` can refresh.
resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "plan_state" {
  name   = "state-access"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.state_read.json
}

# ---------------------------------------------------------------------------
# Deploy role — write, assumed only from the gated `production` environment.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:environment:${var.deploy_environment}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "Project1-CI-Deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_trust.json
}

resource "aws_iam_role_policy" "deploy_state" {
  name   = "state-access"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.state_write.json
}

# Permissions to manage the ETL stack's resources. Scoped to the services the
# main stack creates: S3, Glue, IAM (incl. PassRole), Step Functions,
# DynamoDB, EventBridge, SNS, CloudWatch/Logs.
data "aws_iam_policy_document" "deploy_manage" {
  statement {
    sid = "ManageStackServices"
    actions = [
      "s3:*",
      "glue:*",
      "states:*",
      "dynamodb:*",
      "events:*",
      "sns:*",
      "cloudwatch:*",
      "logs:*",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ManageIam"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:PassRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/etl-*"]
  }

  # Customer-managed policies the stack defines (etl-*-policy, metrics-*-policy).
  statement {
    sid = "ManageCustomerPolicies"
    actions = [
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:ListPolicyVersions",
      "iam:SetDefaultPolicyVersion",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:policy/etl-*",
      "arn:aws:iam::${local.account_id}:policy/metrics-*",
    ]
  }
}

resource "aws_iam_role_policy" "deploy_manage" {
  name   = "manage-etl-stack"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_manage.json
}
