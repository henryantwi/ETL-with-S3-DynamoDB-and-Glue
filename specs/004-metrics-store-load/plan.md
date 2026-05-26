# Implementation Plan: Metrics Store Load

**Branch**: `004-metrics-store-load` | **Date**: 2026-05-25 | **Spec**: `specs/004-metrics-store-load/spec.md`
**Input**: Feature specification from `specs/004-metrics-store-load/spec.md`

## Summary

Load daily `(genre, date)` metric records produced by Phase 3 (`etl-genre-metrics` Parquet output under `processed-data/output/`) into a DynamoDB table `MusicKPIs` keyed on `genre` (PK) + `date` (SK), so downstream consumers can `GetItem` the full record in a single call. Implemented as an AWS Glue Python Shell job (`etl-metrics-writer`) using boto3 `TransactWriteItems` to publish the full per-run record set in one atomic batch (run volume bounded at ≤ 100 records per FR-spec, matching the DynamoDB transaction limit of 100 items / 4 MB). Idempotent via `PutItem` semantics on the composite key — re-runs overwrite the prior record. Reads restricted to named consumer IAM roles via least-privilege policy.

## Technical Context

**Language/Version**: Python 3.9 (AWS Glue Python Shell runtime)
**Primary Dependencies**: boto3 (DynamoDB client + S3 + CloudWatch), pyarrow (Parquet read of Phase 3 output), pytest + moto (local DynamoDB + S3 simulation for tests)
**Storage**: Source — S3 Parquet at `s3://<processed-bucket>/output/date=*/genre=*/` (Phase 3 output). Sink — DynamoDB table `MusicKPIs` (PAY_PER_REQUEST, encryption at rest with AWS-managed key, PITR enabled)
**Testing**: pytest with moto-mocked DynamoDB + S3; integration test for `TransactWriteItems` atomicity (simulated mid-batch failure must leave prior state intact)
**Target Platform**: AWS Glue Python Shell (`pythonshell` command, `--MaxCapacity 0.0625`), Linux
**Project Type**: Batch ETL — writer step (final phase before archive)
**Performance Goals**: Full load of ≤ 100 records completes in ≤ 1 minute (SC-006); per-record `GetItem` p95 < 50 ms (SC-001 — inherent DynamoDB property)
**Constraints**: Atomic publish via single `TransactWriteItems` call (max 100 items, max 4 MB — both match run-volume bound from spec); idempotent re-run via `Put` on `(genre, date)`; structured JSON logs at read/transform/write stages; CloudWatch metrics `RecordsRead`, `RecordsWritten`, `JobDurationSeconds` emitted per run
**Scale/Scope**: ≤ 100 `(genre, date)` records per run (per spec clarification); daily batch; one run per day

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Infrastructure-as-Code | ✅ PASS | DynamoDB table, GSI (if added), `etl-glue-writer-role`, Glue Python Shell job, CloudWatch alarms — all in Terraform |
| II. Least-Privilege IAM | ✅ PASS | Writer role: `dynamodb:PutItem`/`TransactWriteItems`/`DescribeTable` on `MusicKPIs` ARN only; `s3:GetObject`/`ListBucket` on processed bucket; `logs:*` on own log group; `cloudwatch:PutMetricData` (action lacks resource-level constraint — same justification as Phase 3, see Complexity Tracking). Read-only consumer policy: `dynamodb:GetItem`/`Query` on `MusicKPIs` ARN only, attached to named consumer principals (FR-012) |
| III. Encryption Everywhere | ✅ PASS | DynamoDB encryption at rest (AWS-managed key), PITR enabled (constitution Security Requirements); no secrets in code/tf — job receives credentials via attached IAM role |
| IV. Idempotent Pipeline Steps | ✅ PASS | `TransactWriteItems` with `Put` on composite key `(genre, date)` — re-run with same input replaces records, no accumulation (FR-004, SC-002) |
| V. Observable by Default | ✅ PASS | JSON logs at read/transform/write stages; CloudWatch alarm on Glue job failure + DynamoDB throttle metric; metrics `RecordsRead`, `RecordsWritten`, `JobDurationSeconds` per run |
| VI. Schema Validation Before Transform | ✅ PASS | Upstream phases (002 validation, 003 transform) already enforce schema; writer reads pre-validated Parquet |
| VII. Phased Builds | ✅ PASS | Feature 004 = constitution Phase 4 (DynamoDB writer); depends on Phase 1 (S3/IAM) and Phase 3 (Parquet output) complete |

**Post-Phase-1 re-check**: No new violations introduced by data model or contract.

## Project Structure

### Documentation (this feature)

```text
specs/004-metrics-store-load/
├── plan.md              # This file
├── spec.md              # Feature spec (input)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── dynamodb-record-contract.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
glue_jobs/
├── __init__.py
├── validation/                  (002 — existing)
├── genre_metrics/               (003 — existing)
└── metrics_writer/
    ├── __init__.py
    ├── pipeline.py              (entry: getResolvedOptions + boto3 clients + orchestration)
    └── transformations.py       (pure helpers: Parquet→item dict, transact-batch builder, validation)

tests/
└── unit/
    ├── test_validate_files.py        (002 — existing)
    ├── genre_metrics/                (003 — existing)
    └── metrics_writer/
        ├── test_transformations.py        (unit: pure record-mapping + batch builder)
        └── test_pipeline_integration.py   (integration: moto DynamoDB + S3, atomicity, idempotency)

terraform/
├── modules/
│   ├── s3/                      (existing)
│   ├── iam/
│   │   ├── main.tf              (add etl-glue-writer-role + metrics-reader consumer policy)
│   │   ├── variables.tf         (add dynamodb_table_arn)
│   │   └── outputs.tf           (add glue_writer_role_arn, metrics_reader_policy_arn)
│   ├── glue/                    (existing — supports pythonshell via job_type)
│   └── dynamodb/                (NEW)
│       ├── main.tf              (MusicKPIs table: PAY_PER_REQUEST, SSE, PITR, deletion protection)
│       ├── variables.tf         (table_name, project_name, environment)
│       └── outputs.tf           (table_arn, table_name)
├── main.tf                      (add dynamodb module + glue_metrics_writer module + reader policy)
├── outputs.tf                   (add music_kpis_table_arn, music_kpis_table_name)
└── variables.tf                 (unchanged)
```

**Structure Decision**: Single-project layout, new `glue_jobs/metrics_writer/` sibling to `genre_metrics/`. Pure mapping logic in `transformations.py` (no AWS) for testability; `pipeline.py` handles boto3 calls + Glue arg parsing. New `terraform/modules/dynamodb/` module — first DynamoDB resource in the project; encapsulated for reuse should additional KPI tables emerge.

## Complexity Tracking

| Violation | Resource | Justification |
|-----------|----------|---------------|
| Constitution II: wildcard resource | `cloudwatch:PutMetricData` in `etl-glue-writer-role` policy | Same AWS service limitation as Phase 3: IAM does not support resource-level constraints for `cloudwatch:PutMetricData`. `Resource: "*"` required by AWS. Blast radius bounded to custom metric emission only. |
