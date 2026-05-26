# Tasks: Metrics Store Load

**Input**: Design documents from `specs/004-metrics-store-load/`
**Prerequisites**: plan.md ✅, spec.md ✅, data-model.md ✅, contracts/dynamodb-record-contract.md ✅, research.md ✅

**Tests**: Included — integration tests for lookup, idempotency, and atomicity per spec acceptance scenarios.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Maps to user story (US1, US2, US3)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create module directories and Python package stubs before any code is written.

- [x] T001 Create `glue_jobs/metrics_writer/__init__.py` and `tests/unit/metrics_writer/__init__.py` (empty package stubs)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: All Terraform and IAM additions that every user story depends on. No story work starts until this phase is complete.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T002 Create `terraform/modules/dynamodb/variables.tf` with `table_name`, `project_name`, `environment` input variables
- [x] T003 [P] Create `terraform/modules/dynamodb/outputs.tf` with `table_arn` and `table_name` outputs
- [x] T004 Create `terraform/modules/dynamodb/main.tf` — `aws_dynamodb_table` resource `MusicKPIs`: billing_mode PAY_PER_REQUEST, hash_key `genre` (S), range_key `date` (S), server-side encryption with `aws_owned_key`, point_in_time_recovery enabled, deletion_protection_enabled true
- [x] T005 Add `dynamodb_table_arn` variable to `terraform/modules/iam/variables.tf`
- [x] T006 Add `etl-glue-writer-role` IAM role + policy (dynamodb:PutItem, TransactWriteItems, DescribeTable on MusicKPIs ARN; s3:GetObject/ListBucket on processed bucket ARN; cloudwatch:PutMetricData Resource "*"; logs:* on own log group) and `metrics-reader-policy` managed policy (dynamodb:GetItem, Query on MusicKPIs ARN only) to `terraform/modules/iam/main.tf`
- [x] T007 Add `glue_writer_role_arn` and `metrics_reader_policy_arn` outputs to `terraform/modules/iam/outputs.tf`
- [x] T008 Wire `module "dynamodb"`, `module "iam" dynamodb_table_arn`, S3 script upload objects for `metrics_writer/pipeline.py` and `metrics_writer/transformations.py`, and `module "glue_metrics_writer"` (pythonshell, MaxCapacity 0.0625, `--run_date`/`--processed_bucket`/`--metrics_table` args, `--extra-py-files` pointing to transformations script) into `terraform/main.tf`
- [x] T009 Add `music_kpis_table_arn` and `music_kpis_table_name` outputs to `terraform/outputs.tf`

**Checkpoint**: Terraform plan clean — DynamoDB table, IAM roles, Glue Python Shell job all declared. User story implementation can now begin.

---

## Phase 3: User Story 1 — Instant Genre-Date Lookup (Priority: P1) 🎯 MVP

**Goal**: Writer reads Phase 3 Parquet output, maps rows to DynamoDB items, and commits them so a single `GetItem(genre, date)` returns the full six-field metric record.

**Independent Test**: After running `pipeline.py` against a moto-mocked S3 + DynamoDB, call `GetItem(genre="acoustic", date="2024-06-25")` and assert all six fields are present and correctly typed.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T010 [P] [US1] Write unit tests for `parquet_rows_to_ddb_items` (correct S/N/L/M type mapping, Decimal for avg field, top-N lists ≤ cap, zero-listen row omitted) in `tests/unit/metrics_writer/test_transformations.py`
- [x] T011 [P] [US1] Write unit tests for `build_transact_batch` (output is list of Put entries, record_count > 100 raises loud error, empty input returns empty list) in `tests/unit/metrics_writer/test_transformations.py`

### Implementation for User Story 1

- [x] T012 [US1] Implement `parquet_rows_to_ddb_items(rows: list[dict]) -> list[dict]` in `glue_jobs/metrics_writer/transformations.py` — converts Phase 3 Parquet row dicts to DynamoDB item format: integers cast to `int`, avg to `decimal.Decimal`, top_3_songs/top_5_genres as DDB L-of-M; omit any row where `listen_count < 1` (depends on T010 failing)
- [x] T013 [US1] Implement `build_transact_batch(items: list[dict], table_name: str) -> list[dict]` in `glue_jobs/metrics_writer/transformations.py` — returns list of `{"Put": {"TableName": ..., "Item": ...}}` entries using `Put` (not Update) for wholesale-replace semantics; raises `ValueError` loudly if `len(items) > 100` (depends on T011 failing)
- [x] T014 [US1] Implement `glue_jobs/metrics_writer/pipeline.py` — `getResolvedOptions` for `--run_date`, `--processed_bucket`, `--metrics_table`; boto3 S3 + DynamoDB + CloudWatch clients; read Parquet from `s3://<processed_bucket>/output/date=<run_date>/` with pyarrow; call `parquet_rows_to_ddb_items`; call `build_transact_batch`; call `TransactWriteItems`; structured JSON log at read/transform/write stages; emit `RecordsRead`, `RecordsWritten`, `JobDurationSeconds` CloudWatch metrics (depends on T012, T013)
- [x] T015 [US1] Write integration test: moto-mock S3 + DynamoDB, upload fixture Parquet, run pipeline, assert `GetItem(genre="acoustic", date="2024-06-25")` returns item with all six required fields in `tests/unit/metrics_writer/test_pipeline_integration.py` (depends on T014)

**Checkpoint**: US1 fully functional — single GetItem returns complete six-field record after a successful pipeline run.

---

## Phase 4: User Story 2 — Idempotent Reload (Priority: P1)

**Goal**: Re-running the writer with the same input produces the same records — no duplicates, no accumulated counts. Guaranteed by `Put` semantics in `TransactWriteItems`.

**Independent Test**: Run the load step twice with identical fixture Parquet. Assert record count in DynamoDB equals one run's output, and `listen_count` equals the fixture value (not doubled).

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T016 [P] [US2] Write integration test: invoke pipeline twice with same moto-mocked S3 fixture; after second run assert DynamoDB item count equals single-run count and `listen_count` equals fixture value (not doubled) in `tests/unit/metrics_writer/test_pipeline_integration.py`

### Implementation for User Story 2

- [x] T017 [US2] Verify `build_transact_batch` uses `"Put"` action (not `"Update"`) for each entry in `glue_jobs/metrics_writer/transformations.py` — `Put` unconditionally replaces the item, enforcing wholesale-replace idempotency per FR-004 (depends on T016 failing)

**Checkpoint**: US1 + US2 both independently verifiable — idempotent re-runs confirmed.

---

## Phase 5: User Story 3 — Atomic Visibility (Priority: P2)

**Goal**: A mid-run failure leaves no partial state. `TransactWriteItems` is a single call — either all items commit or none do.

**Independent Test**: Using moto, force a `TransactionCanceledException` after the transact call; query any targeted `(genre, date)` and assert prior state (absent or previous value) is preserved — no partial new records visible.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T018 [P] [US3] Write integration test: pre-populate DynamoDB with known prior state; patch `TransactWriteItems` to raise `TransactionCanceledException`; run pipeline; assert all targeted `(genre, date)` items reflect prior state (not new values) in `tests/unit/metrics_writer/test_pipeline_integration.py`

### Implementation for User Story 3

- [x] T019 [US3] Add item-size guard in `glue_jobs/metrics_writer/transformations.py` — before returning the transact batch, estimate serialized size per item and raise `ValueError` loudly if any item exceeds 400 KB (surfaces FR-009 edge case before the transact call, not silently at DynamoDB) (depends on T018 failing)

**Checkpoint**: All three user stories independently verifiable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify observability requirements and run end-to-end quickstart validation.

- [x] T020 [P] Audit structured JSON logging in `glue_jobs/metrics_writer/pipeline.py` — confirm log entries exist at read stage (records_read count), transform stage (records_mapped count), and write stage (records_written count, duration_seconds); all logs serialised as JSON with a `level` field
- [x] T021 [P] Verify CloudWatch metric emission in `glue_jobs/metrics_writer/pipeline.py` — `RecordsRead`, `RecordsWritten`, `JobDurationSeconds` all emitted via `put_metric_data` with `Namespace="ETL/MetricsWriter"` and correct `Value`/`Unit`
- [x] T022 Run `specs/004-metrics-store-load/quickstart.md` validation checklist end-to-end against moto-mocked AWS and confirm all steps pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — **BLOCKS all user stories**
- **User Stories (Phase 3–5)**: All depend on Phase 2 completion
  - US1 (Phase 3) must complete before US2 (Phase 4) — idempotency tests build on pipeline.py
  - US3 (Phase 5) can start after Phase 2; integrates with pipeline.py from Phase 3
- **Polish (Phase 6)**: Depends on Phase 3–5 completion

### User Story Dependencies

- **US1 (P1)**: Start after Phase 2 — core write + lookup path
- **US2 (P1)**: Start after US1 (T014 pipeline.py must exist to test idempotency)
- **US3 (P2)**: Start after US1 (T014 pipeline.py needed for atomicity test)

### Within Each Phase

- T002–T003 parallel (different files)
- T004 depends on T002 (variables used in main.tf)
- T005–T007 parallel (different files within iam module)
- T006 depends on T005 (variable must exist before policy references it)
- T008 depends on T004, T007 (module outputs needed in main.tf)
- T010–T011 parallel (different test functions, same file)
- T012 depends on T010 failing; T013 depends on T011 failing
- T014 depends on T012, T013
- T015, T016, T018 each depend on T014

### Parallel Opportunities

```bash
# Phase 2 — Terraform files (parallel):
T002 terraform/modules/dynamodb/variables.tf
T003 terraform/modules/dynamodb/outputs.tf
T005 terraform/modules/iam/variables.tf
T007 terraform/modules/iam/outputs.tf

# Phase 3 — Tests before impl (parallel):
T010 unit tests for parquet_rows_to_ddb_items
T011 unit tests for build_transact_batch

# Phase 6 — Observability audit (parallel):
T020 JSON logging audit
T021 CloudWatch metrics audit
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T009) — **critical blocker**
3. Complete Phase 3: User Story 1 (T010–T015)
4. **STOP and VALIDATE**: `GetItem(genre, date)` returns six-field record
5. Proceed to US2 + US3 once US1 confirmed working

### Incremental Delivery

1. Phase 1 + 2 → infrastructure ready
2. Phase 3 → US1 lookup works (MVP)
3. Phase 4 → US2 idempotency verified
4. Phase 5 → US3 atomicity confirmed
5. Phase 6 → observability polished

---

## Notes

- [P] tasks = different files, no shared state — safe to run in parallel
- `Put` semantics in `TransactWriteItems` satisfies both US2 (idempotency) and US3 (atomicity) simultaneously
- `decimal.Decimal` is required by boto3 for DynamoDB `N` type on fractional values (`avg_listening_time_per_user`)
- moto v4+ required for `TransactWriteItems` simulation in tests
- Commit after each phase checkpoint before moving to next phase
