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
# IAM roles (US1 + US4): glue validation + Step Functions execution
###############################################################################
module "iam" {
  source = "./modules/iam"

  raw_bucket_arn          = module.raw_data.bucket_arn
  archive_bucket_arn      = module.archive.bucket_arn
  glue_scripts_bucket_arn = module.glue_scripts.bucket_arn
  aws_region              = var.aws_region
  aws_account_id          = var.aws_account_id
}
