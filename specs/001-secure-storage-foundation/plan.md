# Implementation Plan: Secure Storage Foundation

**Branch**: `001-secure-storage-foundation` | **Date**: 2026-05-19 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-secure-storage-foundation/spec.md`

## Summary

Provision three private, encrypted S3 buckets (raw-data, archive, glue-scripts) and two
least-privilege IAM roles (Glue execution, Step Functions execution) via Terraform modules.
Remote state backend (S3 + DynamoDB lock). Python 3.12 smoke tests via moto validate bucket
configuration without hitting real AWS.

## Technical Context

**Language/Version**: Python 3.12 (pinned in `.python-version`); Terraform ≥ 1.6  
**Primary Dependencies**: boto3, pytest, moto[s3] (dev); AWS provider for Terraform  
**Storage**: AWS S3 — three buckets; Terraform remote state in S3 + DynamoDB lock table  
**Testing**: pytest + moto (S3 mock); Terraform plan output checked manually  
**Target Platform**: AWS (ap-southeast-2 or as set in variables)  
**Project Type**: Data pipeline infrastructure (ETL — Phase 1 of 6)  
**Performance Goals**: N/A for storage provisioning  
**Constraints**: SSE-S3 (not KMS) for Phase 1 per spec clarification; no public access; no hardcoded credentials  
**Scale/Scope**: 3 S3 buckets, 2 IAM roles, 1 Terraform remote state backend

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Infrastructure-as-Code | ✅ PASS | All S3/IAM resources in Terraform modules; no console changes |
| II. Least-Privilege IAM | ✅ PASS | Exact ARN references, no `*` actions or resources; roles scoped per service |
| III. Encryption Everywhere | ✅ PASS | SSE-S3 on all 3 buckets; block_public_acls/policy = true; no secrets in code |
| IV. Idempotent Pipeline Steps | N/A | Storage provisioning — no pipeline execution logic in this phase |
| V. Observable by Default | ⚠️ PARTIAL | CloudWatch log permissions included in glue role; alarms deferred to Phase 6 (per constitution) |
| VI. Schema Validation Before Transform | N/A | Phase 2 deliverable |
| VII. Phased Builds | ✅ PASS | This is Phase 1; acceptance criteria defined before Phase 2 begins |

**Security Checklist (Phase 1 scope)**:
- [X] No `*` in any IAM policy action or resource field
- [X] All S3 buckets have `block_public_acls = true`, `block_public_policy = true`
- [X] SSE-S3 enabled on all three buckets
- [X] No secrets or credentials in `.tf`, `.py`, or `.json` files in git
- [X] Terraform state stored remotely with DynamoDB lock

**Violations**: None. Observable-by-Default is partial but CloudWatch alarms are explicitly
Phase 6 per the constitution's build phases. No justification required.

**Role naming note**: User input used generic names (`glue-execution-role`,
`stepfunctions-execution-role`). Constitution specifies exact names. Phase 1 creates only
the roles needed for Phases 1–2. See research.md for resolution.

## Project Structure

### Documentation (this feature)

```text
specs/001-secure-storage-foundation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
terraform/
├── modules/
│   ├── s3/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── iam/
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
├── main.tf
├── backend.tf
├── variables.tf
└── outputs.tf

scripts/
└── smoke_test_s3.py

pyproject.toml
.python-version
uv.lock
```

**Structure Decision**: IaC-first layout. Terraform modules are reusable across phases.
`scripts/` holds Python smoke tests. No `src/` needed — no application code in Phase 1.

## Complexity Tracking

No constitution violations requiring justification.
