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
    "--extra-py-files"    = "s3://${module.glue_scripts.bucket_id}/genre_metrics/transformations.py"
    "--raw_bucket"        = ""
    "--processed_bucket"  = ""
    "--listening_prefix"  = "listening-activity/"
    "--songs_prefix"      = "song-catalog/"
    "--run_date"          = ""
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

  alarm_actions = [var.sns_alarm_topic_arn]
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

  alarm_actions = [var.sns_alarm_topic_arn]
}
