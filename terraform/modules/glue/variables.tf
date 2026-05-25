variable "job_name" {
  type        = string
  description = "Glue job name"
}

variable "script_location" {
  type        = string
  description = "S3 URI of the job script (s3://bucket/key)"
}

variable "role_arn" {
  type        = string
  description = "IAM role ARN for the Glue job"
}

variable "timeout" {
  type        = number
  description = "Max job run time in minutes"
  default     = 5
}

variable "max_retries" {
  type        = number
  description = "Max automatic retries (0 = Step Functions handles retry)"
  default     = 0
}

variable "default_arguments" {
  type        = map(string)
  description = "Default Glue job arguments (overridable at StartJobRun)"
  default     = {}
}

variable "job_type" {
  type        = string
  description = "Glue job type: pythonshell or glueetl"
  default     = "pythonshell"
}

variable "worker_type" {
  type        = string
  description = "Glue worker type (glueetl only): G.1X, G.2X, etc."
  default     = "G.1X"
}

variable "num_workers" {
  type        = number
  description = "Number of Glue workers (glueetl only)"
  default     = 2
}
