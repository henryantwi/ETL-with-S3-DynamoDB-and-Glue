###############################################################################
# US1: raw-data bucket (versioned, encrypted, private)
###############################################################################
module "raw_data" {
  source = "./modules/s3"

  project_name      = var.project_name
  environment       = var.environment
  bucket_suffix     = var.bucket_suffix
  bucket_prefix     = "raw-data"
  enable_versioning = true
  enable_lifecycle  = false
}

###############################################################################
# US2: archive bucket (no versioning, lifecycle to GLACIER@90d, expire@365d)
###############################################################################
module "archive" {
  source = "./modules/s3"

  project_name              = var.project_name
  environment               = var.environment
  bucket_suffix             = var.bucket_suffix
  bucket_prefix             = "archive"
  enable_versioning         = false
  enable_lifecycle          = true
  lifecycle_transition_days = 90
  lifecycle_expiration_days = 365
}

###############################################################################
# US3: glue-scripts bucket (no versioning, encrypted, private)
###############################################################################
module "glue_scripts" {
  source = "./modules/s3"

  project_name      = var.project_name
  environment       = var.environment
  bucket_suffix     = var.bucket_suffix
  bucket_prefix     = "glue-scripts"
  enable_versioning = false
  enable_lifecycle  = false
}

###############################################################################
# Glue validation job (Phase 2)
###############################################################################
resource "aws_s3_object" "validate_files_script" {
  bucket = module.glue_scripts.bucket_id
  key    = "validate_files.py"
  source = "${path.root}/../glue_jobs/validation/validate_files.py"
  etag   = filemd5("${path.root}/../glue_jobs/validation/validate_files.py")
}

module "glue_validate" {
  source = "./modules/glue"

  job_name        = "etl-validate-files"
  script_location = "s3://${module.glue_scripts.bucket_id}/validate_files.py"
  role_arn        = module.iam.glue_validation_role_arn
  timeout         = 5
  max_retries     = 0
  default_arguments = {
    "--raw_bucket"       = ""
    "--listening_prefix" = "listening-activity/"
    "--songs_prefix"     = "song-catalog/"
    "--users_prefix"     = "user-profiles/"
  }
}

###############################################################################
# processed-data bucket (Phase 3: genre metrics output)
###############################################################################
module "processed_data" {
  source = "./modules/s3"

  project_name      = var.project_name
  environment       = var.environment
  bucket_suffix     = var.bucket_suffix
  bucket_prefix     = "processed-data"
  enable_versioning = false
  enable_lifecycle  = false
}

###############################################################################
# DynamoDB table: MusicKPIs (Phase 4)
###############################################################################
module "dynamodb" {
  source = "./modules/dynamodb"

  table_name   = "MusicKPIs"
  project_name = var.project_name
  environment  = var.environment
}

###############################################################################
# IAM roles (US1 + US4): glue validation + Step Functions execution + transform
###############################################################################
module "iam" {
  source = "./modules/iam"

  raw_bucket_arn          = module.raw_data.bucket_arn
  archive_bucket_arn      = module.archive.bucket_arn
  glue_scripts_bucket_arn = module.glue_scripts.bucket_arn
  processed_bucket_arn    = module.processed_data.bucket_arn
  aws_region              = var.aws_region
  aws_account_id          = var.aws_account_id
  dynamodb_table_arn      = module.dynamodb.table_arn
}

###############################################################################
# Glue genre-metrics job (Phase 3: PySpark G.1X × 2)
###############################################################################
resource "aws_s3_object" "genre_metrics_script" {
  bucket = module.glue_scripts.bucket_id
  key    = "genre_metrics/pipeline.py"
  source = "${path.root}/../glue_jobs/genre_metrics/pipeline.py"
  etag   = filemd5("${path.root}/../glue_jobs/genre_metrics/pipeline.py")
}

resource "aws_s3_object" "genre_metrics_transformations" {
  bucket = module.glue_scripts.bucket_id
  key    = "genre_metrics/transformations.py"
  source = "${path.root}/../glue_jobs/genre_metrics/transformations.py"
  etag   = filemd5("${path.root}/../glue_jobs/genre_metrics/transformations.py")
}

module "glue_genre_metrics" {
  source = "./modules/glue"

  job_name        = "etl-genre-metrics"
  script_location = "s3://${module.glue_scripts.bucket_id}/genre_metrics/pipeline.py"
  role_arn        = module.iam.glue_transform_role_arn
  job_type        = "glueetl"
  worker_type     = "G.1X"
  num_workers     = 2
  timeout         = 30
  max_retries     = 0
  default_arguments = {
    "--extra-py-files"   = "s3://${module.glue_scripts.bucket_id}/genre_metrics/transformations.py"
    "--raw_bucket"       = ""
    "--processed_bucket" = ""
    "--listening_prefix" = "listening-activity/"
    "--songs_prefix"     = "song-catalog/"
    "--run_date"         = ""
  }
}

###############################################################################
# Glue metrics-writer job (Phase 4: Python Shell)
###############################################################################
resource "aws_s3_object" "metrics_writer_pipeline" {
  bucket = module.glue_scripts.bucket_id
  key    = "metrics_writer/pipeline.py"
  source = "${path.root}/../glue_jobs/metrics_writer/pipeline.py"
  etag   = filemd5("${path.root}/../glue_jobs/metrics_writer/pipeline.py")
}

resource "aws_s3_object" "metrics_writer_transformations" {
  bucket = module.glue_scripts.bucket_id
  key    = "metrics_writer/transformations.py"
  source = "${path.root}/../glue_jobs/metrics_writer/transformations.py"
  etag   = filemd5("${path.root}/../glue_jobs/metrics_writer/transformations.py")
}

module "glue_metrics_writer" {
  source = "./modules/glue"

  job_name        = "etl-metrics-writer"
  script_location = "s3://${module.glue_scripts.bucket_id}/metrics_writer/pipeline.py"
  role_arn        = module.iam.glue_writer_role_arn
  job_type        = "pythonshell"
  timeout         = 5
  max_retries     = 0
  default_arguments = {
    "--extra-py-files"   = "s3://${module.glue_scripts.bucket_id}/metrics_writer/transformations.py"
    "--processed_bucket" = ""
    "--metrics_table"    = module.dynamodb.table_name
    "--run_date"         = ""
  }
}

###############################################################################
# SNS topic for CloudWatch alarm notifications
###############################################################################
resource "aws_sns_topic" "etl_alerts" {
  name = "${var.project_name}-pipeline-alerts"
}

###############################################################################
# CloudWatch alarm: etl-metrics-writer job failures
###############################################################################
resource "aws_cloudwatch_metric_alarm" "metrics_writer_failures" {
  alarm_name          = "etl-metrics-writer-failures"
  namespace           = "Glue"
  metric_name         = "glue.driver.aggregate.numFailedTasks"
  statistic           = "Sum"
  period              = 300
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1

  dimensions = {
    JobName = "etl-metrics-writer"
  }

  alarm_actions = [aws_sns_topic.etl_alerts.arn]
}

###############################################################################
# CloudWatch alarm: etl-genre-metrics job failures
###############################################################################
resource "aws_cloudwatch_metric_alarm" "genre_metrics_failures" {
  alarm_name          = "etl-genre-metrics-failures"
  namespace           = "Glue"
  metric_name         = "glue.driver.aggregate.numFailedTasks"
  statistic           = "Sum"
  period              = 300
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1

  dimensions = {
    JobName = "etl-genre-metrics"
  }

  alarm_actions = [aws_sns_topic.etl_alerts.arn]
}

###############################################################################
# Glue archive job (Python Shell) — move raw files to archive after pipeline
###############################################################################
resource "aws_s3_object" "archive_files_script" {
  bucket = module.glue_scripts.bucket_id
  key    = "archive/archive_files.py"
  source = "${path.root}/../glue_jobs/archive/archive_files.py"
  etag   = filemd5("${path.root}/../glue_jobs/archive/archive_files.py")
}

module "glue_archive_files" {
  source = "./modules/glue"

  job_name        = "etl-archive-files"
  script_location = "s3://${module.glue_scripts.bucket_id}/archive/archive_files.py"
  role_arn        = module.iam.glue_archive_role_arn
  job_type        = "pythonshell"
  timeout         = 10
  max_retries     = 0
  default_arguments = {
    "--raw_bucket"       = module.raw_data.bucket_id
    "--archive_bucket"   = module.archive.bucket_id
    "--listening_prefix" = "listening-activity/"
    "--songs_prefix"     = "song-catalog/"
    "--users_prefix"     = "user-profiles/"
  }
}

###############################################################################
# Step Functions state machine — ETL pipeline orchestration
###############################################################################
resource "aws_cloudwatch_log_group" "sfn_etl_pipeline" {
  name              = "/aws/states/etl-pipeline"
  retention_in_days = 30
}

###############################################################################
# EventBridge trigger — auto-start ETL pipeline when new data lands in S3
#
# EventBridge S3 notifications require the source bucket to have
# EventBridge notifications enabled.  The rule fires on ANY ObjectCreated
# event under listening-activity/ prefix so the pipeline starts as soon as
# a new batch of streams arrives.
###############################################################################

resource "aws_s3_bucket_notification" "raw_data_eventbridge" {
  bucket      = module.raw_data.bucket_id
  eventbridge = true
}

resource "aws_cloudwatch_event_rule" "s3_listening_activity" {
  name        = "etl-s3-listening-activity-uploaded"
  description = "Trigger ETL pipeline when a new file lands in listening-activity/ prefix"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [module.raw_data.bucket_id]
      }
      object = {
        key = [{ prefix = "listening-activity/" }]
      }
    }
  })
}

# Route uploads to the dispatch queue (not directly to Step Functions) so a
# burst of uploads coalesces into one run and runs never overlap. EventBridge ->
# SQS needs a queue resource policy (below), not an IAM role.
resource "aws_cloudwatch_event_target" "sqs_pipeline_dispatch" {
  rule = aws_cloudwatch_event_rule.s3_listening_activity.name
  arn  = aws_sqs_queue.pipeline_dispatch.arn
}

###############################################################################
# Pipeline dispatcher: SQS coalescing + serialization in front of Step Functions
###############################################################################

resource "aws_sqs_queue" "pipeline_dispatch_dlq" {
  name                      = "${var.project_name}-pipeline-dispatch-dlq"
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "pipeline_dispatch" {
  name                       = "${var.project_name}-pipeline-dispatch"
  visibility_timeout_seconds = var.dispatcher_queue_visibility_timeout_seconds
  message_retention_seconds  = 86400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.pipeline_dispatch_dlq.arn
    maxReceiveCount     = var.dispatcher_max_receive_count
  })
}

# Allow the EventBridge rule (and only it) to send to the queue.
data "aws_iam_policy_document" "pipeline_dispatch_queue" {
  statement {
    sid       = "AllowEventBridgeSend"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.pipeline_dispatch.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.s3_listening_activity.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "pipeline_dispatch" {
  queue_url = aws_sqs_queue.pipeline_dispatch.id
  policy    = data.aws_iam_policy_document.pipeline_dispatch_queue.json
}

# Package the handler at plan time — no separate CI build step.
data "archive_file" "pipeline_dispatcher" {
  type        = "zip"
  source_file = "${path.root}/../lambda/pipeline_dispatcher/handler.py"
  output_path = "${path.module}/build/pipeline_dispatcher.zip"
}

data "aws_iam_policy_document" "dispatcher_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pipeline_dispatcher" {
  name               = "${var.project_name}-pipeline-dispatcher-role"
  assume_role_policy = data.aws_iam_policy_document.dispatcher_trust.json
}

data "aws_iam_policy_document" "pipeline_dispatcher" {
  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${var.project_name}-pipeline-dispatcher*"]
  }
  statement {
    sid    = "ConsumeQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.pipeline_dispatch.arn]
  }
  statement {
    sid       = "DispatchExecutions"
    effect    = "Allow"
    actions   = ["states:StartExecution", "states:ListExecutions"]
    resources = [aws_sfn_state_machine.etl_pipeline.arn]
  }
}

resource "aws_iam_policy" "pipeline_dispatcher" {
  name        = "${var.project_name}-pipeline-dispatcher-policy"
  description = "Dispatcher Lambda: consume dispatch queue, start/list etl-pipeline executions, write logs"
  policy      = data.aws_iam_policy_document.pipeline_dispatcher.json
}

resource "aws_iam_role_policy_attachment" "pipeline_dispatcher" {
  role       = aws_iam_role.pipeline_dispatcher.name
  policy_arn = aws_iam_policy.pipeline_dispatcher.arn
}

resource "aws_lambda_function" "pipeline_dispatcher" {
  function_name                  = "${var.project_name}-pipeline-dispatcher"
  role                           = aws_iam_role.pipeline_dispatcher.arn
  runtime                        = "python3.11"
  handler                        = "handler.handler"
  filename                       = data.archive_file.pipeline_dispatcher.output_path
  source_code_hash               = data.archive_file.pipeline_dispatcher.output_base64sha256
  timeout                        = 30
  reserved_concurrent_executions = 1 # single consumer -> the RUNNING guard cannot race itself

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.etl_pipeline.arn
      QUEUE_URL         = aws_sqs_queue.pipeline_dispatch.id
      WAIT_SECONDS      = "60"
      RAW_BUCKET        = module.raw_data.bucket_id
      ARCHIVE_BUCKET    = module.archive.bucket_id
      PROCESSED_BUCKET  = module.processed_data.bucket_id
      METRICS_TABLE     = module.dynamodb.table_name
      LISTENING_PREFIX  = "listening-activity/"
      SONGS_PREFIX      = "song-catalog/"
      USERS_PREFIX      = "user-profiles/"
    }
  }
}

# Coalesce a burst into one invocation via the batching window + large batch size.
resource "aws_lambda_event_source_mapping" "pipeline_dispatch" {
  event_source_arn                   = aws_sqs_queue.pipeline_dispatch.arn
  function_name                      = aws_lambda_function.pipeline_dispatcher.arn
  batch_size                         = 100
  maximum_batching_window_in_seconds = var.dispatcher_batching_window_seconds
  function_response_types            = ["ReportBatchItemFailures"]
}

resource "aws_sfn_state_machine" "etl_pipeline" {
  name     = "etl-pipeline"
  role_arn = module.iam.stepfunctions_role_arn

  definition = templatefile("${path.root}/../step_functions/etl_pipeline.asl.json", {})

  logging_configuration {
    level                  = "ERROR"
    include_execution_data = false
    log_destination        = "${aws_cloudwatch_log_group.sfn_etl_pipeline.arn}:*"
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}
