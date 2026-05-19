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
# NOTE: StringLike on etl-* job ARNs is a Phase 1 placeholder. Tighten to
# exact ARNs in Phase 2 once Glue jobs exist (see plan.md research Decision 6).
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
