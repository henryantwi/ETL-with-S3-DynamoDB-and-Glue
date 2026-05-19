# Data Model: Secure Storage Foundation

**Phase 1 output** | Branch: `001-secure-storage-foundation`

---

## S3 Buckets

### RawStorage (`raw-data-{suffix}`)

| Property | Value |
|----------|-------|
| Purpose | Incoming CSV files (songs, streams, users) |
| Versioning | Enabled |
| Encryption | SSE-S3 (AES256) |
| Public access | Block all (acls + policy + ignore + restrict) |
| Lifecycle | None in Phase 1; archive after 90d in Phase 5 |
| IAM readers | `etl-glue-validation-role`, `etl-glue-transform-role` |
| IAM writers | External producers (not managed by this phase) |

### ArchiveStorage (`archive-{suffix}`)

| Property | Value |
|----------|-------|
| Purpose | Post-processed files; prevents re-processing |
| Versioning | Disabled |
| Encryption | SSE-S3 (AES256) |
| Public access | Block all |
| Lifecycle | Delete after 365d (Phase 5) |
| IAM writers | `etl-stepfunctions-role` (via S3 CopyObject — Phase 5) |

### ScriptStorage (`glue-scripts-{suffix}`)

| Property | Value |
|----------|-------|
| Purpose | Glue job Python scripts |
| Versioning | Disabled |
| Encryption | SSE-S3 (AES256) |
| Public access | Block all |
| IAM readers | `etl-glue-validation-role`, `etl-glue-transform-role`, `etl-glue-writer-role` |

**Naming convention**: `{bucket-purpose}-{project_name}-{environment}` or use a random
suffix to ensure global uniqueness. Set via Terraform variable `bucket_suffix`.

---

## IAM Roles (Phase 1 scope)

### `etl-glue-validation-role`

**Trust**: `glue.amazonaws.com`

**Managed policy** `etl-glue-validation-policy`:

| Action | Resource |
|--------|----------|
| `s3:GetObject` | `arn:aws:s3:::raw-data-{suffix}/*` |
| `s3:ListBucket` | `arn:aws:s3:::raw-data-{suffix}` |
| `s3:GetObject` | `arn:aws:s3:::glue-scripts-{suffix}/*` |
| `s3:ListBucket` | `arn:aws:s3:::glue-scripts-{suffix}` |
| `logs:CreateLogGroup` | `arn:aws:logs:{region}:{account}:log-group:/aws-glue/jobs/*` |
| `logs:CreateLogStream` | `arn:aws:logs:{region}:{account}:log-group:/aws-glue/jobs/*:*` |
| `logs:PutLogEvents` | `arn:aws:logs:{region}:{account}:log-group:/aws-glue/jobs/*:*` |

**Denied (implicit)**: All other actions including `s3:PutObject`, `dynamodb:*`.

---

### `etl-stepfunctions-role`

**Trust**: `states.amazonaws.com`

**Managed policy** `etl-stepfunctions-policy` (Phase 1 scope):

| Action | Resource |
|--------|----------|
| `glue:StartJobRun` | `arn:aws:glue:{region}:{account}:job/etl-*` |
| `glue:GetJobRun` | `arn:aws:glue:{region}:{account}:job/etl-*` |

**Note**: `StringLike` on `etl-*` prefix used in Phase 1 because exact job ARNs don't
exist yet. Tighten to exact ARNs when Glue jobs are created in Phase 2.
S3 archive permissions (`s3:CopyObject`, `s3:DeleteObject`) added in Phase 5.

---

## Terraform Remote State

| Resource | Value |
|----------|-------|
| S3 bucket | `terraform-state-{account_id}` (bootstrapped separately) |
| DynamoDB table | `terraform-locks` |
| DynamoDB key | `LockID` (String) |
| Encryption | SSE-S3 (auto on new tables) |

---

## Terraform Module Inputs/Outputs

### `modules/s3`

**Variables**:

| Name | Type | Description |
|------|------|-------------|
| `project_name` | string | Used in bucket name |
| `environment` | string | e.g., `dev`, `prod` |
| `bucket_suffix` | string | Unique suffix for global uniqueness |
| `enable_versioning` | bool | Enable S3 versioning |

**Outputs**:

| Name | Description |
|------|-------------|
| `bucket_arn` | ARN of created bucket |
| `bucket_id` | Bucket name |

### `modules/iam`

**Variables**:

| Name | Type | Description |
|------|------|-------------|
| `raw_bucket_arn` | string | ARN of raw-data bucket |
| `glue_scripts_bucket_arn` | string | ARN of glue-scripts bucket |
| `aws_region` | string | Deployment region |
| `aws_account_id` | string | Account ID for log group ARNs |

**Outputs**:

| Name | Description |
|------|-------------|
| `glue_validation_role_arn` | ARN of `etl-glue-validation-role` |
| `stepfunctions_role_arn` | ARN of `etl-stepfunctions-role` |
