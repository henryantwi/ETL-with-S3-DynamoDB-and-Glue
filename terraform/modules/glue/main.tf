resource "aws_glue_job" "this" {
  name        = var.job_name
  role_arn    = var.role_arn
  timeout     = var.timeout
  max_retries = var.max_retries

  command {
    name            = "pythonshell"
    python_version  = "3"
    script_location = var.script_location
  }

  # Python Shell uses MaxCapacity instead of NumberOfWorkers
  max_capacity = 0.0625

  default_arguments = merge(
    {
      "--enable-continuous-cloudwatch-log" = "true"
      "--enable-metrics"                   = "true"
    },
    var.default_arguments
  )
}
