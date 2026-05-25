resource "aws_glue_job" "this" {
  name        = var.job_name
  role_arn    = var.role_arn
  timeout     = var.timeout
  max_retries = var.max_retries

  dynamic "command" {
    for_each = var.job_type == "glueetl" ? [] : [1]
    content {
      name            = "pythonshell"
      python_version  = "3"
      script_location = var.script_location
    }
  }

  dynamic "command" {
    for_each = var.job_type == "glueetl" ? [1] : []
    content {
      name            = "glueetl"
      python_version  = "3"
      script_location = var.script_location
    }
  }

  # Python Shell uses MaxCapacity; glueetl uses NumberOfWorkers + WorkerType
  max_capacity     = var.job_type == "glueetl" ? null : 0.0625
  number_of_workers = var.job_type == "glueetl" ? var.num_workers : null
  worker_type      = var.job_type == "glueetl" ? var.worker_type : null
  glue_version     = var.job_type == "glueetl" ? "4.0" : null

  default_arguments = merge(
    {
      "--enable-continuous-cloudwatch-log" = "true"
      "--enable-metrics"                   = "true"
    },
    var.default_arguments
  )
}
