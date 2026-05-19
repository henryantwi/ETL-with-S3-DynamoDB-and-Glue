# Research: Secure Storage Foundation

**Phase 0 output** | Branch: `001-secure-storage-foundation`

---

## Decision 1: IAM Role Naming

**Decision**: Use constitution-specified role names, not generic names from user input.

| User Input Name | Constitution Name | Phase Created |
|-----------------|------------------|---------------|
| `glue-execution-role` | `etl-glue-validation-role` | Phase 1 (smoke test only) |
| `stepfunctions-execution-role` | `etl-stepfunctions-role` | Phase 1 |

Phase 1 creates `etl-glue-validation-role` and `etl-stepfunctions-role` only.
Remaining roles (`etl-glue-transform-role`, `etl-glue-writer-role`, `etl-eventbridge-role`)
are deferred to the phases that need them.

**Rationale**: Constitution is authoritative. Generic names would require renaming later,
causing Terraform state drift.

**Alternatives considered**: Create all 5 roles in Phase 1 — rejected because roles for
non-existent resources (DynamoDB, transform job) cannot reference real ARNs yet.

---

## Decision 2: Terraform Remote State Backend

**Decision**: Dedicated `terraform-state-{account_id}` S3 bucket + `terraform-locks`
DynamoDB table for remote state. This bucket is NOT one of the three data buckets.

**Rationale**: State bucket must exist before `terraform init` on the main config. It is
bootstrapped separately (manual `aws s3 mb` once, or a separate `bootstrap/` Terraform
root). Mixing state with data buckets creates circular dependency.

**Alternatives considered**: Local state — rejected per constitution Security Requirements
("Remote backend MUST be used; state file MUST NOT be committed to git").

**How to bootstrap**:
```bash
aws s3 mb s3://terraform-state-<account_id> --region <region>
aws dynamodb create-table \
  --table-name terraform-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region <region>
```

---

## Decision 3: S3 Versioning Scope

**Decision**: Enable versioning on `raw-data` bucket only. `archive` and `glue-scripts`
buckets do not have versioning enabled.

**Rationale**: Spec assumption explicit: "Versioning is required on raw storage only."
Constitution security requirement: "Enable on raw bucket to recover from accidental
overwrites." Archive files are derived — reproducible from raw + pipeline re-run.

**Alternatives considered**: Versioning on all three — rejected as unnecessary cost
for derived/static content.

---

## Decision 4: Encryption Type

**Decision**: SSE-S3 (`AES256`) on all three buckets for Phase 1.

**Rationale**: Spec clarification (2026-05-19): "AWS-managed keys (SSE-S3) — no
customer-managed KMS required." Constitution says "SSE-S3 minimum; SSE-KMS preferred
for raw data bucket" — Phase 1 uses minimum; KMS upgrade deferred to a later phase
if compliance requires.

**Alternatives considered**: SSE-KMS — available but deferred per explicit spec
clarification to keep Phase 1 simple.

---

## Decision 5: Python Environment

**Decision**: uv-managed project, Python 3.12. No `requirements.txt`. `uv.lock` is
source of truth. Dev dependencies: `boto3`, `pytest`, `moto[s3]`.

**Rationale**: User specified uv. `moto[s3]` extra required (not just `moto`) to get
S3-specific mock backend.

**How to install**:
```bash
uv sync
uv run pytest scripts/smoke_test_s3.py
```

---

## Decision 6: Step Functions Role Permissions (Phase 1 Scope)

**Decision**: `etl-stepfunctions-role` in Phase 1 only gets `glue:StartJobRun` and
`glue:GetJobRun`. S3 archive permissions (`s3:CopyObject`, `s3:DeleteObject`) deferred
to Phase 5 when the archive state is implemented.

**Rationale**: Phase 1 has no Glue jobs yet. Role ARN references in policies must point
to resources that exist or will be created in same apply. Using `*` on Glue job ARNs
forbidden by constitution. Placeholder approach: policy created with condition
`StringLike` on job name prefix `etl-*` — tighten to exact ARNs in Phase 2.

**Alternatives considered**: Empty role with no policies — rejected because role must
be testable in Phase 1 acceptance criteria.
