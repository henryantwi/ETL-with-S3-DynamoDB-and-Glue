# Implementation Plan: Data Validation

**Branch**: `002-data-validation` | **Date**: 2026-05-20 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-data-validation/spec.md`

## Summary

AWS Glue Python Shell job reads CSV headers from three S3 prefixes under the raw-data
bucket, validates required fields against hardcoded schemas, collects all failures before
raising, and exits non-zero on any failure so Step Functions catches the failed state.
No distributed compute — Python Shell is sufficient for header-only reads. Terraform
provisions the Glue job resource using the existing `etl-glue-validation-role` from Phase 1.

## Technical Context

**Language/Version**: Python 3.12 (pinned in `.python-version`); Terraform ≥ 1.6
**Primary Dependencies**: boto3 (S3 header reads); csv (stdlib, no pandas); Python logging → CloudWatch via Glue runtime
**Storage**: S3 raw-data bucket (read-only); glue-scripts bucket (job script location)
**Testing**: pytest + moto[s3]; `uv run pytest glue_jobs/validation/`
**Target Platform**: AWS Glue Python Shell (ap-southeast-2 or as set in variables)
**Project Type**: ETL data pipeline — Phase 2 of 6
**Performance Goals**: Validation completes in < 5 minutes per run (Glue job timeout = 5 min); header-only reads minimize I/O
**Constraints**: No pandas; stdlib csv module only; no full-file load into memory; 0 Glue job retries (Step Functions owns retry)
**Scale/Scope**: 3 files per run; header row only per file; single-DPU Python Shell job

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Infrastructure-as-Code | ✅ PASS | Glue job resource in `terraform/modules/glue/`; no console changes |
| II. Least-Privilege IAM | ✅ PASS | Reuses `etl-glue-validation-role` from Phase 1; role already scoped to `s3:GetObject` on raw + glue-scripts, CloudWatch Logs on `/aws-glue/jobs/*` |
| III. Encryption Everywhere | ✅ PASS | No credentials in code; S3 access via IAM role; no secrets in params |
| IV. Idempotent Pipeline Steps | ✅ PASS | Validation is read-only; re-running with same input always produces same result |
| V. Observable by Default | ✅ PASS | Python `logging` → CloudWatch `/aws-glue/jobs/*` automatically; structured JSON log lines per FR-010 |
| VI. Schema Validation Before Transform | ✅ PASS | This IS the schema validation step; required columns hardcoded per constitution + spec clarification |
| VII. Phased Builds | ✅ PASS | Phase 2 of 6; Phase 1 (S3 + IAM) complete and accepted |

**Security Checklist (Phase 2 additions)**:
- [ ] No `*` in any new IAM action or resource (no new IAM resources — inherited from Phase 1)
- [ ] Glue job parameters contain no credentials — only bucket name and prefix strings
- [ ] Job script uploaded to glue-scripts bucket (not embedded in Terraform)
- [ ] Glue job `--extra-py-files` not used — stdlib + boto3 only (boto3 pre-installed in Glue runtime)

**Violations**: None.

## Project Structure

### Documentation (this feature)

```text
specs/002-data-validation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
glue_jobs/
└── validation/
    ├── validate_files.py       # Glue Python Shell job entrypoint
    └── test_validate_files.py  # pytest + moto unit tests

terraform/
└── modules/
    └── glue/
        ├── main.tf             # aws_glue_job resource
        ├── variables.tf        # module inputs
        └── outputs.tf          # job name output
```

**Structure Decision**: Glue jobs live in `glue_jobs/<job-name>/` alongside their tests.
Terraform module mirrors the Phase 1 pattern (`modules/s3/`, `modules/iam/`). No `src/` —
no application code, only pipeline job scripts.

## Complexity Tracking

No constitution violations requiring justification.
