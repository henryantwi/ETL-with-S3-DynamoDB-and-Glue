###############################################################################
# etl-glue-validation-role
###############################################################################

data "aws_iam_policy_document" "glue_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue_validation" {
  name               = "etl-glue-validation-role"
  assume_role_policy = data.aws_iam_policy_document.glue_trust.json
}

data "aws_iam_policy_document" "glue_validation" {
  statement {
    sid    = "ReadRawData"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = ["${var.raw_bucket_arn}/*"]
  }

  statement {
    sid    = "ListRawData"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
    ]
    resources = [var.raw_bucket_arn]
  }

  statement {
    sid    = "ReadGlueScripts"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = ["${var.glue_scripts_bucket_arn}/*"]
  }

  statement {
    sid    = "ListGlueScripts"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
    ]
    resources = [var.glue_scripts_bucket_arn]
  }

  statement {
    sid    = "GlueJobLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws-glue/jobs/*",
    ]
  }

  statement {
    sid    = "GlueJobLogStreams"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws-glue/jobs/*:*",
    ]
  }
}

resource "aws_iam_policy" "glue_validation" {
  name        = "etl-glue-validation-policy"
  description = "Read raw-data + glue-scripts; write CloudWatch Logs scoped to /aws-glue/jobs/*"
  policy      = data.aws_iam_policy_document.glue_validation.json
}

resource "aws_iam_role_policy_attachment" "glue_validation" {
  role       = aws_iam_role.glue_validation.name
  policy_arn = aws_iam_policy.glue_validation.arn
}

###############################################################################
# etl-stepfunctions-role
#
# Phase 1 scope: glue:StartJobRun + glue:GetJobRun (StringLike etl-*) plus
# raw GetObject / archive PutObject / raw DeleteObject for archive-move flow.
#
# POLICY GUARD: StringLike on `etl-*` Glue job ARN prefix is a Phase 1
# placeholder. Phase 2 MUST tighten this to exact job ARNs once Glue jobs
# exist (`arn:aws:glue:{region}:{account}:job/etl-validation`, etc.). No `*`
# in any action or resource — confirmed by `terraform plan` audit (T024).
# See specs/001-secure-storage-foundation/research.md Decision 6.
###############################################################################

data "aws_iam_policy_document" "stepfunctions_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "stepfunctions" {
  name               = "etl-stepfunctions-role"
  assume_role_policy = data.aws_iam_policy_document.stepfunctions_trust.json
}

data "aws_iam_policy_document" "stepfunctions" {
  statement {
    sid    = "GlueJobControl"
    effect = "Allow"
    actions = [
      "glue:StartJobRun",
      "glue:GetJobRun",
    ]
    resources = [
      "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:job/etl-*",
    ]
  }

  statement {
    sid       = "ReadRawObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.raw_bucket_arn}/*"]
  }

  statement {
    sid       = "WriteArchiveObjects"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.archive_bucket_arn}/*"]
  }

  statement {
    sid       = "DeleteRawObjects"
    effect    = "Allow"
    actions   = ["s3:DeleteObject"]
    resources = ["${var.raw_bucket_arn}/*"]
  }
}

resource "aws_iam_policy" "stepfunctions" {
  name        = "etl-stepfunctions-policy"
  description = "Least-priv Step Functions role: start Glue etl-* jobs + archive move"
  policy      = data.aws_iam_policy_document.stepfunctions.json
}

resource "aws_iam_role_policy_attachment" "stepfunctions" {
  role       = aws_iam_role.stepfunctions.name
  policy_arn = aws_iam_policy.stepfunctions.arn
}

###############################################################################
# etl-glue-transform-role
###############################################################################

resource "aws_iam_role" "glue_transform" {
  name               = "etl-glue-transform-role"
  assume_role_policy = data.aws_iam_policy_document.glue_trust.json
}

data "aws_iam_policy_document" "glue_transform" {
  statement {
    sid    = "ReadRawData"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = ["${var.raw_bucket_arn}/*"]
  }

  statement {
    sid    = "ListRawData"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
    ]
    resources = [var.raw_bucket_arn]
  }

  statement {
    sid    = "ReadWriteProcessedData"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${var.processed_bucket_arn}/*"]
  }

  statement {
    sid    = "ListProcessedData"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
    ]
    resources = [var.processed_bucket_arn]
  }

  statement {
    sid    = "ReadGlueScripts"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = ["${var.glue_scripts_bucket_arn}/*"]
  }

  statement {
    sid    = "ListGlueScripts"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
    ]
    resources = [var.glue_scripts_bucket_arn]
  }

  statement {
    sid    = "GlueJobLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws-glue/jobs/etl-genre-metrics",
      "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws-glue/jobs/etl-genre-metrics:*",
    ]
  }

  # AWS does not support resource-level constraints for cloudwatch:PutMetricData
  statement {
    sid    = "CloudWatchMetrics"
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricData",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "glue_transform" {
  name        = "etl-glue-transform-policy"
  description = "Least-priv: read raw + read/write processed + CloudWatch metrics for etl-genre-metrics"
  policy      = data.aws_iam_policy_document.glue_transform.json
}

resource "aws_iam_role_policy_attachment" "glue_transform" {
  role       = aws_iam_role.glue_transform.name
  policy_arn = aws_iam_policy.glue_transform.arn
}
