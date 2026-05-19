<!--
SYNC IMPACT REPORT
==================
Version change: [UNVERSIONED] → 1.0.0
Added sections: Core Principles (I–VII), Security Requirements, Build Phases, Governance
Removed sections: N/A (initial authoring)
Templates requiring updates:
  ✅ plan-template.md — Constitution Check section references these principles
  ✅ spec-template.md — FR/SC patterns align with pipeline acceptance criteria
  ✅ tasks-template.md — Phase structure mirrors build phases defined here
Deferred TODOs:
  - TODO(RATIFICATION_DATE): Set to 2026-05-19 (today, first authoring)
-->

# Music Streaming ETL Pipeline Constitution

## Core Principles

### I. Infrastructure-as-Code (NON-NEGOTIABLE)

Every AWS resource — S3 buckets, Glue jobs, Step Functions state machines, DynamoDB tables,
IAM roles, CloudWatch alarms — MUST be defined in Terraform. Manual console changes are
forbidden. A resource that cannot be recreated by `terraform apply` from a clean account
does not exist in this project.

**Rationale**: Reproducibility, auditability, and disaster-recovery depend on IaC parity.

### II. Least-Privilege IAM

Every IAM role MUST be scoped to the exact actions and exact resource ARNs it requires.
Wildcard actions (`*`) and wildcard resources (`*`) are forbidden. Each role MUST have a
single owning service and MUST NOT be shared across services.

Roles required:

| Role | Service | Key Permissions |
|------|---------|-----------------|
| `etl-glue-validation-role` | AWS Glue (Python Shell) | `s3:GetObject` on raw bucket, `logs:CreateLogGroup`, `logs:PutLogEvents` |
| `etl-glue-transform-role` | AWS Glue (PySpark) | `s3:GetObject` on raw bucket, `s3:PutObject` on processed bucket, `logs:*` on own log group |
| `etl-glue-writer-role` | AWS Glue (Python Shell) | `dynamodb:PutItem`, `dynamodb:BatchWriteItem` on `MusicKPIs` table, `logs:*` on own log group |
| `etl-stepfunctions-role` | Step Functions | `glue:StartJobRun`, `glue:GetJobRun`, `s3:CopyObject`, `s3:DeleteObject` on raw→archive path |
| `etl-eventbridge-role` | EventBridge (scheduler) | `states:StartExecution` on the ETL state machine only |

**Rationale**: Blast radius from a compromised role is bounded. Audits pass. No lateral movement.

### III. Encryption Everywhere

- S3 buckets MUST use SSE-S3 (AES-256) minimum; SSE-KMS preferred for raw data bucket.
- DynamoDB table MUST enable encryption at rest (AWS-managed key minimum).
- No credentials, tokens, or secrets MUST appear in code, Terraform vars files committed to
  git, or Glue job parameters. Use AWS Secrets Manager or SSM Parameter Store.
- S3 buckets MUST block all public access.

**Rationale**: Regulatory baseline for any data containing user PII (user_country, user_age).

### IV. Idempotent Pipeline Steps

Each Glue job and each Step Functions state MUST be idempotent: re-running with the same
input MUST produce the same output without duplicating DynamoDB records or corrupting state.
DynamoDB writes MUST use `PutItem` with a deterministic composite key (not append-only).

**Rationale**: S3 files arrive at irregular intervals; retries and replays are routine.

### V. Observable by Default

- Every Glue job MUST emit structured log lines (JSON) to CloudWatch Logs.
- Step Functions MUST have CloudWatch logging enabled (ERROR level minimum, ALL preferred).
- A CloudWatch alarm MUST exist for Step Functions execution failures.
- A CloudWatch alarm MUST exist for each Glue job failure metric.
- Dashboard or alarm for DynamoDB write throttles MUST exist.

**Rationale**: Silent failures in batch pipelines cause stale KPIs surfaced to business.

### VI. Schema Validation Before Transform

The validation Glue job (Python Shell) MUST run before the PySpark transform and MUST cause
the Step Functions execution to FAIL (not skip) if any required column is absent from any
source CSV. Required columns:

- `songs.csv`: `track_id`, `track_genre`, `duration_ms`
- `streams*.csv`: `user_id`, `track_id`, `listen_time`
- `users.csv`: `user_id`

**Rationale**: A transform that silently drops missing joins produces wrong KPIs with no error.

### VII. Phased, Independently Testable Builds

Work MUST proceed through the phases below in order. Each phase MUST have a passing acceptance
test before the next phase begins. No phase may be skipped.

**Rationale**: Catching misconfigured IAM or S3 policies in Phase 1 costs minutes; catching
them in Phase 5 costs days.

## Security Requirements

- **No hardcoded credentials**: Glue jobs receive AWS credentials via the attached IAM role only.
- **S3 versioning**: Enable on raw bucket to recover from accidental overwrites.
- **S3 lifecycle**: Archive processed files after 90 days; delete after 365 days.
- **DynamoDB point-in-time recovery (PITR)**: MUST be enabled on `MusicKPIs` table.
- **CloudTrail**: AWS CloudTrail MUST be active in the deployment region for API audit.
- **Terraform state**: Remote backend (S3 + DynamoDB lock table) MUST be used; state file
  MUST NOT be committed to git.

### Security Checklist (pre-completion gate)

- [ ] No `*` in any IAM policy action or resource field
- [ ] All S3 buckets have `block_public_acls = true`, `block_public_policy = true`
- [ ] SSE enabled on raw and processed S3 buckets
- [ ] DynamoDB encryption at rest enabled
- [ ] DynamoDB PITR enabled
- [ ] No secrets or credentials in `.tf`, `.py`, or `.json` files in git
- [ ] Terraform state stored remotely with lock
- [ ] CloudWatch alarms configured for Glue failures and Step Functions failures
- [ ] CloudTrail active

## Build Phases

### Phase 1 — Terraform: S3 + IAM

**Deliverables**:
- `terraform/modules/s3/`: reusable S3 module (versioning, SSE, public access block, lifecycle)
- `terraform/modules/iam/`: reusable IAM module (least-privilege roles, exact ARNs, no wildcards)
- `terraform/main.tf`: instantiates modules for raw, archive, glue-scripts buckets and required roles
- `terraform/iam.tf` (or module instantiation): roles required for Phases 1–2 (`etl-glue-validation-role`, `etl-stepfunctions-role`) + inline policies (exact ARNs, no wildcards). Remaining roles created in phase they are first needed: `etl-glue-transform-role` (Phase 3), `etl-glue-writer-role` (Phase 4), `etl-eventbridge-role` (Phase 6).
- `terraform/backend.tf`: remote state config
- `terraform/variables.tf` + `terraform/outputs.tf`

**Acceptance Criteria**:
- `terraform plan` shows zero errors
- `terraform apply` creates all resources
- `aws s3api get-bucket-encryption` confirms SSE on both buckets
- `aws s3api get-public-access-block` confirms all 4 block-public flags true
- `aws iam simulate-principal-policy` confirms each role can perform its required actions
  and is denied actions outside its scope
- Upload `data/songs.csv` manually → object appears in raw bucket

---

### Phase 2 — Glue Validation Job (Python Shell)

**Deliverables**:
- `glue/validate_schema.py`: reads S3 CSV headers, asserts required columns, exits non-zero on failure
- `terraform/glue.tf` (partial): Python Shell job resource with `etl-glue-validation-role`

**Acceptance Criteria**:
- Job succeeds when all CSVs have required columns
- Job fails (exit code ≠ 0, CloudWatch log shows column name) when a column is removed from test CSV
- CloudWatch log group `/aws-glue/jobs/validate-schema` contains structured JSON output
- IAM: job cannot write to S3 (simulate-principal-policy confirms `s3:PutObject` denied)

---

### Phase 3 — Glue PySpark Transform Job

**Deliverables**:
- `glue/transform_kpis.py`: joins songs + streams + users, computes 6 KPIs, writes Parquet to processed bucket
- `terraform/glue.tf` (updated): PySpark job resource with `etl-glue-transform-role`
- `data/` folder: sample CSVs covering ≥ 3 genres, ≥ 2 days, ≥ 10 users for local testing

**KPI logic**:
- `listen_count`: `COUNT(stream_id)` per genre per day
- `unique_listeners`: `COUNT(DISTINCT user_id)` per genre per day
- `total_listen_ms`: `SUM(duration_ms)` per genre per day
- `avg_listen_ms_per_user`: `total_listen_ms / unique_listeners`
- `top_3_songs`: rank by `listen_count` per genre per day, take top 3
- `top_5_genres`: rank genres by `listen_count` per day, take top 5

**Acceptance Criteria**:
- `terraform apply` deploys job without error
- Job run with sample data produces Parquet in processed bucket
- KPI values verified by hand against sample CSVs for 1 genre + 1 day
- `top_3_songs` contains ≤ 3 entries per (genre, date)
- `top_5_genres` contains ≤ 5 entries per date
- IAM: job cannot write to DynamoDB (simulate-principal-policy confirms `dynamodb:PutItem` denied)

---

### Phase 4 — Glue DynamoDB Writer Job (Python Shell)

**Deliverables**:
- `glue/write_dynamodb.py`: reads Parquet from processed bucket, batch-writes to `MusicKPIs`
- `terraform/dynamodb.tf`: table definition
- `terraform/glue.tf` (updated): writer job resource with `etl-glue-writer-role`

**DynamoDB Table Design**:

| Attribute | Type | Role |
|-----------|------|------|
| `genre` | String (S) | Partition Key |
| `date` | String (S) `YYYY-MM-DD` | Sort Key |
| `listen_count` | Number (N) | Attribute |
| `unique_listeners` | Number (N) | Attribute |
| `total_listen_ms` | Number (N) | Attribute |
| `avg_listen_ms_per_user` | Number (N) | Attribute |
| `top_3_songs` | List (L) | Attribute — list of `{track_id, track_name, listen_count}` |
| `top_5_genres_rank` | Number (N) | Attribute — rank within top-5 list for this date |

Table name: `MusicKPIs`
Billing: PAY_PER_REQUEST
Encryption: AWS-managed key
PITR: enabled

Query patterns supported:
- All KPIs for genre `X` on date `Y`: `GetItem(PK=X, SK=Y)`
- All dates for genre `X`: `Query(PK=X)`
- Top-5 genres for date `Y`: `Query` on a GSI `date-index` (PK=`date`, SK=`top_5_genres_rank`)

GSI `date-index`: PK=`date` (S), SK=`top_5_genres_rank` (N)

**Acceptance Criteria**:
- `terraform apply` creates table and GSI
- Writer job populates table from Phase 3 Parquet output
- `aws dynamodb get-item` returns correct values for 2 test (genre, date) pairs
- GSI query for a date returns ≤ 5 items in rank order
- Re-running writer job with same data does NOT duplicate records (idempotency)
- PITR enabled confirmed via `aws dynamodb describe-continuous-backups`

---

### Phase 5 — Step Functions Orchestration

**Deliverables**:
- `terraform/stepfunctions.tf`: state machine definition (inline ASL JSON or file reference)
- State machine wires: Validate → Transform → Write → Archive

**State Machine Design (ASL)**:

```
StartExecution
  └─► ValidateSchema (Glue Python Shell)
        ├─ Success ──► TransformKPIs (Glue PySpark)
        │               ├─ Success ──► WriteDynamoDB (Glue Python Shell)
        │               │               ├─ Success ──► ArchiveFiles (SDK integration: S3 CopyObject + DeleteObject)
        │               │               │               └─ Success ──► ExecutionSucceeded
        │               │               └─ Failure ──► NotifyFailure (CloudWatch / SNS optional)
        │               └─ Failure ──► NotifyFailure
        └─ Failure ──► NotifyFailure
```

Retry policy per Glue task:
- `MaxAttempts: 2`
- `IntervalSeconds: 30`
- `BackoffRate: 2`
- `ErrorEquals: ["Glue.AWSGlueException", "States.TaskFailed"]`

Catch:
- All errors MUST transition to a `Fail` state with `Cause` and `Error` populated.

Archive step:
- S3 CopyObject: `s3://raw-bucket/<key>` → `s3://archive-bucket/YYYY-MM-DD/<key>`
- S3 DeleteObject on raw originals after successful archive copy

**Acceptance Criteria**:
- `terraform apply` deploys state machine
- Manual execution with sample data completes all 4 states successfully
- Deliberate schema failure (remove column from test CSV) causes execution to FAIL at
  ValidateSchema, not silently skip
- CloudWatch execution log shows all state transitions
- Re-execution with same date is idempotent (DynamoDB records overwritten, not duplicated)

---

### Phase 6 — Scheduling + Monitoring

**Deliverables**:
- `terraform/eventbridge.tf`: daily schedule rule → Step Functions
- `terraform/cloudwatch.tf`: alarms for Glue failures, Step Functions failures, DynamoDB throttles
- `docs/runbook.md`: how to trigger manually, how to re-run a failed date, how to check logs

**Acceptance Criteria**:
- EventBridge rule triggers state machine at configured daily time
- CloudWatch alarm fires when Step Functions execution fails (test with synthetic failure)
- Alarm fires when Glue job fails (test with bad CSV)
- All alarms visible in CloudWatch console / described via CLI
- `docs/runbook.md` documents all operational procedures

## Governance

This constitution supersedes all informal agreements, verbal decisions, and prior
documentation. Amendments require:

1. A written rationale (PR description or ADR)
2. Update to version line following semantic versioning (see below)
3. Propagation check: update plan-template, spec-template, tasks-template if affected
4. Approval by project lead before merge

**Versioning policy**:
- MAJOR: removal or redefinition of a principle, or breaking change to DynamoDB schema
- MINOR: new principle, new build phase, material expansion
- PATCH: wording, typo, clarification

All PRs MUST include a Constitution Check confirming no violations are introduced.
Complexity violations (e.g., wildcard IAM) MUST be justified in `plan.md` Complexity Tracking
before merge. Unjustified violations MUST block merge.

Runtime development guidance: `docs/runbook.md` (created in Phase 6).

**Version**: 1.1.0 | **Ratified**: 2026-05-19 | **Last Amended**: 2026-05-19
