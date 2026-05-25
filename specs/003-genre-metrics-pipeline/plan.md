# Implementation Plan: Genre Metrics Pipeline

**Branch**: `003-genre-metrics-pipeline` | **Date**: 2026-05-25 | **Spec**: `specs/003-genre-metrics-pipeline/spec.md`  
**Input**: Feature specification from `specs/003-genre-metrics-pipeline/spec.md`

## Summary

Transform validated listening-activity and song-catalog Parquet files into daily genre-level performance metrics (total plays, distinct users, listening time, top-3 songs, top-5 genres), written atomically to S3 Parquet partitioned by `date=<value>/genre=<value>/`. Implemented as an AWS Glue PySpark job (`etl-genre-metrics`) with structured JSON logging and CloudWatch metrics, wired into the existing ETL pipeline as Phase 3 of the Step Functions state machine.

## Technical Context

**Language/Version**: Python 3.10 (AWS Glue 4.0 runtime, Spark 3.3.0)  
**Primary Dependencies**: PySpark (via Glue 4.0), boto3 (S3 staging swap + CloudWatch metrics), pyarrow (Parquet test fixtures), pytest + pyspark (local test execution)  
**Storage**: S3 Parquet — input: raw-data bucket (`listening-activity/`, `song-catalog/`); output: processed-data bucket (`output/date=*/genre=*/`) with staging prefix (`staging/<run_id>/`)  
**Testing**: pytest + PySpark local mode (unit); moto + boto3 (S3 integration); no Glue runtime needed locally  
**Target Platform**: AWS Glue PySpark (G.1X worker × 2, `glueetl` command), Linux  
**Project Type**: Batch ETL pipeline (PySpark transform job)  
**Performance Goals**: Full run (join + aggregation + write) ≤ 30 minutes for representative daily dataset (SC-007)  
**Constraints**: Atomic writes via staging-prefix swap; idempotent re-run for same date; structured JSON logs at each pipeline stage; CloudWatch metrics `RecordsRead`, `RecordsWritten`, `JobDurationSeconds` emitted per run regardless of output record count  
**Scale/Scope**: Daily batch; one run per day; representative dataset size not specified (30-min SLA is the target bound)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Infrastructure-as-Code | ✅ PASS | Processed S3 bucket, `etl-glue-transform-role`, Glue PySpark job all defined in Terraform; no manual console resources |
| II. Least-Privilege IAM | ✅ PASS | `etl-glue-transform-role` scoped to: `s3:GetObject`/`s3:ListBucket` on raw bucket; `s3:GetObject`/`s3:PutObject`/`s3:DeleteObject`/`s3:ListBucket` on processed bucket; `logs:*` scoped to own log group; `cloudwatch:PutMetricData` (no resource ARN possible for this action) |
| III. Encryption Everywhere | ✅ PASS | Processed S3 bucket uses SSE-S3; no new secrets or credentials in code |
| IV. Idempotent Pipeline Steps | ✅ PASS | Staging-prefix swap: same-date re-run overwrites the output partition; no append-only behavior |
| V. Observable by Default | ✅ PASS | FR-010: JSON logs at read/join/aggregation/write stages; FR-011: CloudWatch `RecordsRead`, `RecordsWritten`, `JobDurationSeconds` |
| VI. Schema Validation Before Transform | ✅ PASS | This job runs after the validation job passes; receives pre-validated Parquet |
| VII. Phased Builds | ✅ PASS | Feature 003 = Phase 3 of constitution; depends on Phase 1 (S3/IAM) and Phase 2 (validation job) both complete |

**Post-Phase-1 re-check**: No new violations introduced by data model or contracts.

## Project Structure

### Documentation (this feature)

```text
specs/003-genre-metrics-pipeline/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── s3-output-contract.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
glue_jobs/
├── __init__.py
├── validation/
│   ├── __init__.py
│   └── validate_files.py       (002 — existing)
└── genre_metrics/
    ├── __init__.py
    ├── pipeline.py              (entry point — getResolvedOptions + Spark session + orchestration)
    └── transformations.py       (pure PySpark transforms — join, aggregate, top-N; no AWS calls)

tests/
└── unit/
    ├── test_validate_files.py   (002 — existing)
    └── genre_metrics/
        ├── test_transformations.py   (unit: pure PySpark, no AWS)
        └── test_pipeline_integration.py  (integration: moto S3 + staging-swap logic)

terraform/
├── modules/
│   ├── s3/           (existing — no changes needed)
│   ├── iam/
│   │   ├── main.tf          (add etl-glue-transform-role)
│   │   ├── variables.tf     (add processed_bucket_arn)
│   │   └── outputs.tf       (add glue_transform_role_arn)
│   └── glue/
│       ├── main.tf          (extend: glueetl command + worker vars)
│       ├── variables.tf     (add job_type, worker_type, num_workers)
│       └── outputs.tf       (unchanged)
├── main.tf                  (add processed-data bucket + glue_transform module instantiation)
├── outputs.tf               (add processed_bucket_id, processed_bucket_arn)
└── variables.tf             (unchanged)
```

**Structure Decision**: Single-project layout extending the existing `glue_jobs/` tree. Transforms extracted to `transformations.py` for pure-PySpark unit testing without AWS runtime. Pipeline entry point in `pipeline.py` handles Glue args, Spark session, boto3 calls (S3 swap, CloudWatch), and orchestrates transforms.

## Complexity Tracking

N/A — no constitution violations.
