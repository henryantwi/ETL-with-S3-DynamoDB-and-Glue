# Tasks: Secure Storage Foundation

**Input**: Design documents from `/specs/001-secure-storage-foundation/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization — Python env, Terraform skeleton, remote state bootstrap

- [X] T001 Create repository directory structure per plan.md (`terraform/modules/s3/`, `terraform/modules/iam/`, `scripts/`)
- [X] T002 Create `pyproject.toml` with uv project config and dev dependencies: `boto3`, `pytest`, `moto[s3]`
- [X] T003 Create `.python-version` pinned to `3.12`
- [X] T004 [P] Create `terraform/variables.tf` with top-level variables (`project_name`, `environment`, `bucket_suffix`, `aws_region`, `aws_account_id`)
- [X] T005 [P] Create `terraform/outputs.tf` skeleton (bucket ARNs, role ARNs)
- [X] T006 Document remote state bootstrap commands in `scripts/bootstrap.sh` (one-time `aws s3 mb` + DynamoDB create-table)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Terraform remote state backend and S3 module — must exist before any bucket story can be provisioned

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T007 Create `terraform/backend.tf` with S3 backend config (`terraform-state-{account_id}`, `terraform-locks` DynamoDB table, `LockID` key)
- [X] T008 Create `terraform/modules/s3/variables.tf` with module inputs: `project_name`, `environment`, `bucket_suffix`, `enable_versioning`, `enable_lifecycle` (default false)
- [X] T009 Create `terraform/modules/s3/outputs.tf` with `bucket_arn` and `bucket_id` outputs
- [X] T010 Implement `terraform/modules/s3/main.tf` — `aws_s3_bucket`, `aws_s3_bucket_server_side_encryption_configuration` (AES256), `aws_s3_bucket_public_access_block` (all four block flags true), `aws_s3_bucket_versioning` (controlled by variable)

**Checkpoint**: S3 module complete — user story bucket instantiation can now proceed

---

## Phase 3: User Story 1 — Raw Data Ingestion Point (Priority: P1) 🎯 MVP

**Goal**: Private, versioned, encrypted raw-data bucket; validator role can read from it; no public access.

**Independent Test**: Upload a CSV file to `raw-data-{suffix}` and confirm it is present, encrypted, and not publicly accessible; confirm `etl-glue-validation-role` can `s3:GetObject` from it.

### Implementation for User Story 1

- [X] T011 [US1] Instantiate raw-data bucket in `terraform/main.tf` using `modules/s3` with `enable_versioning = true` and `bucket_suffix` variable
- [X] T012 [US1] Create `terraform/modules/iam/variables.tf` with inputs: `raw_bucket_arn`, `archive_bucket_arn`, `glue_scripts_bucket_arn`, `aws_region`, `aws_account_id`
- [X] T013 [US1] Create `terraform/modules/iam/outputs.tf` with `glue_validation_role_arn` and `stepfunctions_role_arn`
- [X] T014 [US1] Implement `etl-glue-validation-role` in `terraform/modules/iam/main.tf` — trust `glue.amazonaws.com`; managed policy `etl-glue-validation-policy` with exact ARN `s3:GetObject`/`s3:ListBucket` on raw-data and glue-scripts buckets, plus scoped CloudWatch Logs permissions (`/aws-glue/jobs/*`)
- [X] T015 [US1] Instantiate IAM module in `terraform/main.tf` passing raw-data, archive, and glue-scripts bucket ARNs from module outputs (archive ARN wiring soft-depends on T017 — sequence T017 before T015 or use a null placeholder until archive bucket exists)
- [X] T016 [US1] Write moto smoke test in `scripts/smoke_test_s3.py` — assert raw-data bucket exists, SSE-S3 enabled, versioning enabled, all public access blocks true

**Checkpoint**: User Story 1 fully functional — raw bucket provisioned, validator role created, smoke test passes

---

## Phase 4: User Story 2 — Processed File Archive (Priority: P2)

**Goal**: Private, encrypted archive bucket; no versioning; no public access; separate from raw-data bucket.

**Independent Test**: Confirm archive bucket exists, SSE-S3 enabled, versioning disabled, all public access blocks true; confirm no policy allows public reads.

### Implementation for User Story 2

- [X] T017 [US2] Instantiate archive bucket in `terraform/main.tf` using `modules/s3` with `enable_versioning = false` and distinct `bucket_suffix`/name prefix (`archive-`)
- [X] T018 [US2] Extend moto smoke test in `scripts/smoke_test_s3.py` — assert archive bucket exists, SSE-S3 enabled, versioning disabled, all public access blocks true

**Checkpoint**: User Story 2 complete — archive bucket provisioned and independently verified

---

## Phase 5: User Story 3 — Script Storage for Automated Jobs (Priority: P2)

**Goal**: Private, encrypted glue-scripts bucket; validator/transform/writer roles can read scripts; no public access.

**Independent Test**: Confirm glue-scripts bucket exists, SSE-S3 enabled, versioning disabled, public access blocked; confirm `etl-glue-validation-role` policy includes `s3:GetObject` on this bucket.

### Implementation for User Story 3

- [X] T019 [US3] Instantiate glue-scripts bucket in `terraform/main.tf` using `modules/s3` with `enable_versioning = false` and name prefix `glue-scripts-`
- [X] T020 [US3] Verify `etl-glue-validation-policy` in `terraform/modules/iam/main.tf` already includes `s3:GetObject`/`s3:ListBucket` on `glue_scripts_bucket_arn` (added in T014 — confirm reference is wired correctly via module output)
- [X] T021 [US3] Extend moto smoke test in `scripts/smoke_test_s3.py` — assert glue-scripts bucket exists, SSE-S3 enabled, versioning disabled, all public access blocks true

**Checkpoint**: User Story 3 complete — all three buckets provisioned and verified

---

## Phase 6: User Story 4 — Least-Privilege Processing Identity (Priority: P1)

**Goal**: `etl-stepfunctions-role` with scoped Glue permissions; no `*` actions or resources; no hardcoded credentials anywhere.

**Independent Test**: Confirm `etl-stepfunctions-role` trust is `states.amazonaws.com`; policy allows `glue:StartJobRun`/`glue:GetJobRun` with `StringLike` on `etl-*` job prefix; no `*` actions or resources in any policy; no credentials in any `.tf`, `.py`, or config file.

### Implementation for User Story 4

- [ ] T022 [US4] Implement `etl-stepfunctions-role` in `terraform/modules/iam/main.tf` — trust `states.amazonaws.com`; managed policy `etl-stepfunctions-policy` with: (1) `glue:StartJobRun`/`glue:GetJobRun` scoped to `arn:aws:glue:{region}:{account}:job/etl-*` via `StringLike`; (2) `s3:GetObject` on raw bucket objects; (3) `s3:PutObject` on archive bucket objects; (4) `s3:DeleteObject` on raw bucket objects (archive move pattern)
- [ ] T023 [US4] Add policy guard comment in `terraform/modules/iam/main.tf` noting Phase 2 will tighten `StringLike etl-*` to exact ARNs once Glue jobs exist
- [ ] T024 [US4] Run `terraform validate` and `terraform plan` (dry run) — confirm no `*` actions or resources in plan output; document expected output in `scripts/smoke_test_s3.py` header comment
- [ ] T025 [US4] Scan all `.tf`, `.py`, `pyproject.toml` files for hardcoded credential patterns; confirm zero findings (manual grep check documented in quickstart.md)

**Checkpoint**: User Story 4 complete — all IAM roles least-privilege, no credentials in code

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Security checklist sign-off, final validation, documentation

- [ ] T026 [P] Verify security checklist from plan.md — no `*` in any IAM action/resource, all blocks true, SSE-S3 on all buckets, no secrets in git, remote state configured
- [ ] T027 Run quickstart.md validation scenario end-to-end: `uv sync`, `uv run pytest scripts/smoke_test_s3.py` — all assertions pass
- [ ] T028 [P] Update `terraform/outputs.tf` with all final outputs: `raw_bucket_arn`, `archive_bucket_arn`, `glue_scripts_bucket_arn`, `glue_validation_role_arn`, `stepfunctions_role_arn`
- [ ] T029 [P] Add `.gitignore` entries: `**/.terraform/`, `*.tfstate`, `*.tfstate.backup`, `*.tfvars` (credentials), `.terraform.lock.hcl` optional keep
- [ ] T030 Validate `terraform fmt -check` passes on all `.tf` files
- [ ] T031 Add `aws_s3_bucket_lifecycle_configuration` to `terraform/modules/s3/main.tf` gated by `enable_lifecycle` variable. Instantiate on archive bucket in `terraform/main.tf` with: transition to GLACIER at 90 days, expiration at 365 days (per constitution Security Requirements)
- [ ] T032 Extend moto smoke test in `scripts/smoke_test_s3.py` to assert archive bucket lifecycle config present (transition 90d → GLACIER, expiration 365d) when enabled

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2; P1 priority — start first
- **US2 (Phase 4)**: Depends on Phase 2; can run in parallel with US1 after Phase 2 done
- **US3 (Phase 5)**: Depends on Phase 2; can run in parallel with US1/US2 after Phase 2 done; IAM wiring depends on T014 (US1)
- **US4 (Phase 6)**: Depends on Phase 2; IAM module scaffolding depends on T012–T013 (US1); P1 priority
- **Polish (Phase 7)**: Depends on all user stories complete

### User Story Dependencies

- **US1 (P1)**: After Foundational — no story dependencies
- **US2 (P2)**: After Foundational — no story dependencies; independently testable
- **US3 (P2)**: After Foundational — IAM policy references US1's glue-scripts bucket ARN (T014 must be complete)
- **US4 (P1)**: After Foundational — IAM module scaffold from US1 (T012–T013) must exist

### Parallel Opportunities

- T004, T005 in Phase 1 can run in parallel
- T008, T009 in Phase 2 can run in parallel (after T007)
- After Phase 2: US2 (T017–T018) can run in parallel with US1 implementation
- T026, T028, T029 in Phase 7 can run in parallel

---

## Parallel Example: Phases 4 & 5 after Phase 2

```bash
# After Foundational (T007–T010) completes:
# Parallel track A — US2:
Task: "T017 Instantiate archive bucket in terraform/main.tf"
Task: "T018 Extend smoke test for archive bucket"

# Parallel track B — US3:
Task: "T019 Instantiate glue-scripts bucket in terraform/main.tf"
Task: "T020 Verify IAM policy wiring for glue-scripts bucket"
Task: "T021 Extend smoke test for glue-scripts bucket"
```

---

## Implementation Strategy

### MVP First (US1 + US4 — both P1)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL)
3. Complete Phase 3: User Story 1 (raw bucket + validator role)
4. Complete Phase 6: User Story 4 (stepfunctions role — shares IAM module)
5. **STOP and VALIDATE**: `uv run pytest scripts/smoke_test_s3.py`; `terraform plan` clean
6. Demo: three-bucket + two-role infrastructure provisioned with least-privilege policies

### Incremental Delivery

1. Setup + Foundational → S3 module ready
2. US1 → raw bucket + validator role → smoke test passes (MVP)
3. US4 → stepfunctions role → security checklist clears
4. US2 → archive bucket → no-reprocessing guarantee
5. US3 → glue-scripts bucket → script storage secured
6. Polish → final validation, docs

---

## Notes

- No test tasks generated — spec.md does not request TDD; moto smoke tests are implementation deliverables (not optional TDD artifacts)
- [P] tasks touch different files with no inter-task dependencies
- Commit after each phase checkpoint
- `terraform plan` is the acceptance gate for IaC tasks — no real AWS resources needed during development
- `uv run pytest scripts/smoke_test_s3.py` is the acceptance gate for Python tasks (moto mocks all S3 calls)
