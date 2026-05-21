# Tasks: Data Validation

**Input**: Design documents from `/specs/002-data-validation/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅

**Organization**: All three user stories are P1 — validation is a single atomic gate. Tasks
grouped so setup and foundational work unlock all three independently-testable stories.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US3)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Directory layout, pyproject update, Terraform Glue module skeleton

- [X] T001 Create `glue_jobs/validation/` directory and add empty `__init__.py`
- [X] T002 Add `pytest` and `moto[s3]` to `pyproject.toml` dev dependencies (already present — verify and add `awsglue` stub if needed for local import)
- [X] T003 [P] Create `terraform/modules/glue/variables.tf` with inputs: `job_name`, `script_location`, `role_arn`, `timeout`, `max_retries`, `default_arguments`
- [X] T004 [P] Create `terraform/modules/glue/outputs.tf` with `job_name` output

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core validation logic and Terraform Glue module — must exist before any story test can pass

**⚠️ CRITICAL**: No user story test can pass until this phase is complete

- [X] T005 Implement `terraform/modules/glue/main.tf` — `aws_glue_job` resource: `GlueVersion = "3.0"`, `Command.Name = "pythonshell"`, `Command.PythonVersion = "3"`, `NumberOfWorkers` not set (Python Shell uses `MaxCapacity = 0.0625`), `Timeout` and `MaxRetries` from variables, `DefaultArguments` from variable map
- [X] T006 Instantiate Glue module in `terraform/main.tf` — pass `script_location = "s3://${module.glue_scripts.bucket_id}/validate_files.py"`, `role_arn = module.iam.glue_validation_role_arn`, `job_name = "etl-validate-files"`, `timeout = 5`, `max_retries = 0`
- [X] T007 Implement core validation library in `glue_jobs/validation/validate_files.py`:
  - `FileSchema` dataclass: `prefix`, `file_type`, `required_fields: frozenset`
  - `ValidationResult` dataclass: `file_key`, `file_type`, `status`, `missing_fields`, `failure_reason`
  - `SCHEMAS` constant: dict mapping prefix → `FileSchema` for all three file types (listening-activity, song-catalog, user-profiles)
  - `read_csv_header(s3_client, bucket, key) -> list[str]` — streams first 4096 bytes via `Range` header, parses with `csv.reader`, returns header row; raises on missing key (NoSuchKey) or decode error
  - `validate_file(s3_client, bucket, prefix) -> ValidationResult` — lists objects at prefix, picks first CSV, calls `read_csv_header`, checks required fields, returns result
  - `validate_all(s3_client, bucket, args) -> list[ValidationResult]` — iterates all three schemas unconditionally, collects results, returns full list (does NOT raise here)
  - `main()` — calls `getResolvedOptions`, builds s3 client, calls `validate_all`, logs each result as JSON, raises `ValueError` with all failure details if any failures exist
- [X] T008 Add structured JSON logger helper in `glue_jobs/validation/validate_files.py` — `log_result(logger, result)` emits one JSON line per result with keys: `level`, `job`, `file_key`, `file_type`, `status`, `missing_fields`, `failure_reason`

**Checkpoint**: Core library complete — all three user story tests can now be written and run

---

## Phase 3: User Story 1 — Valid Files Pass Through (Priority: P1) 🎯 MVP

**Goal**: All three files present with correct headers → validation passes, no exception raised.

**Independent Test**: Create three moto-mocked S3 objects with correct headers under their respective prefixes. Call `validate_all`. Assert no failures and `main()` exits without exception.

### Implementation for User Story 1

- [ ] T009 [US1] Write moto test `test_all_valid_files_pass` in `glue_jobs/validation/test_validate_files.py` — mock S3 bucket, upload three valid CSVs to correct prefixes, call `validate_all`, assert `len(failures) == 0`
- [ ] T010 [US1] Write moto test `test_valid_listening_activity_accepted` — assert `ValidationResult.status == "PASS"` for listening-activity file with `user_id,track_id,listened_at` header
- [ ] T011 [US1] Write moto test `test_valid_song_catalog_accepted` — assert pass for song-catalog file with `track_id,song_name,artist_name,genre,duration` header
- [ ] T012 [US1] Write moto test `test_valid_user_profiles_accepted` — assert pass for user-profiles file with `user_id,username,country` header

**Checkpoint**: User Story 1 fully verified — happy path confirmed

---

## Phase 4: User Story 2 — Missing Fields Halt the Pipeline (Priority: P1)

**Goal**: Any missing required field → all failures collected, exception raised, no partial advance.

**Independent Test**: Upload listening-activity CSV without `track_id`. Call `validate_all`. Assert failure record names the file and lists `track_id`. Assert `main()` raises.

### Implementation for User Story 2

- [ ] T013 [US2] Write moto test `test_missing_field_in_listening_activity` — upload CSV with `user_id,listened_at` (missing `track_id`), assert `ValidationResult.missing_fields == ["track_id"]`, `failure_reason == "field-error"`
- [ ] T014 [US2] Write moto test `test_multiple_missing_fields_in_song_catalog` — upload CSV without `artist_name` and `genre`, assert both appear in `missing_fields`
- [ ] T015 [US2] Write moto test `test_one_failure_blocks_all_files` — upload one valid + one invalid file, call `validate_all`, assert exception raised and valid files NOT advanced (i.e., function raises, not silently continues)
- [ ] T016 [US2] Write moto test `test_multiple_file_failures_collected_together` — upload two invalid CSVs (different missing fields), assert single exception references both failures with all missing fields

**Checkpoint**: User Story 2 fully verified — failure collection and halt confirmed

---

## Phase 5: User Story 3 — Unreadable, Empty, or Missing Files Are Rejected (Priority: P1)

**Goal**: Empty file, missing S3 key, or corrupt/unreadable file → failure logged, pipeline halts.

**Independent Test**: Create empty S3 object at listening-activity prefix. Assert `ValidationResult.failure_reason == "empty"` and `main()` raises.

### Implementation for User Story 3

- [ ] T017 [US3] Write moto test `test_missing_file_rejected` — do NOT upload object at expected prefix, call `validate_all`, assert `failure_reason == "missing"`
- [ ] T018 [US3] Write moto test `test_empty_file_rejected` — upload zero-byte object at prefix, assert `failure_reason == "empty"`
- [ ] T019 [US3] Write moto test `test_unreadable_file_rejected` — upload object with non-UTF-8 binary bytes at prefix, assert `failure_reason == "unreadable"`
- [ ] T020 [US3] Write moto test `test_infrastructure_failure_halts_pipeline` — simulate S3 client raising `ClientError` (e.g., permissions), assert exception propagates and is logged before re-raise

**Checkpoint**: User Story 3 fully verified — structural failure modes all handled

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Terraform wiring validation, security scan, credential audit, `terraform fmt`

- [ ] T021 [P] Run `terraform validate` — confirm Glue module + instantiation in `main.tf` have no errors
- [ ] T022 [P] Run `terraform fmt -check -recursive` — confirm all `.tf` files clean
- [ ] T023 [P] Run `uv run pytest glue_jobs/validation/ -v` — confirm all tests pass (moto, no real AWS)
- [ ] T024 Scan `glue_jobs/`, `terraform/`, `pyproject.toml` for hardcoded credential patterns (`AKIA...`, `aws_secret_access_key`) — confirm zero findings
- [ ] T025 Verify `validate_files.py` never imports pandas or any non-stdlib/boto3 library — stdlib `csv` module only
- [ ] T026 [P] Update `terraform/outputs.tf` to expose `glue_validation_job_name` output from module
- [ ] T027 Add `glue_jobs/` and `.pytest_cache/` to `.gitignore` pyc/cache patterns (verify, append if missing)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 core library (T007–T008)
- **US2 (Phase 4)**: Depends on Phase 2 core library; can run in parallel with US1 after T007–T008
- **US3 (Phase 5)**: Depends on Phase 2 core library; can run in parallel with US1/US2 after T007–T008
- **Polish (Phase 6)**: Depends on all user stories complete

### User Story Dependencies

- **US1**: After Phase 2 — no story dependencies
- **US2**: After Phase 2 — no story dependencies; `validate_all` collect-all behavior (T007) must be correct
- **US3**: After Phase 2 — `read_csv_header` error-handling paths (T007) must be implemented

### Parallel Opportunities

- T003, T004 in Phase 1 can run in parallel
- T009–T012 in Phase 3 can all run in parallel (different test functions, same file)
- T013–T016 in Phase 4 can all run in parallel
- T017–T020 in Phase 5 can all run in parallel
- T021, T022, T023, T024, T025, T026 in Phase 6 can run in parallel

---

## Parallel Example: Phase 3 (US1 tests)

```bash
# All four US1 tests can be written in parallel (different test functions):
Task: "T009 test_all_valid_files_pass in test_validate_files.py"
Task: "T010 test_valid_listening_activity_accepted in test_validate_files.py"
Task: "T011 test_valid_song_catalog_accepted in test_validate_files.py"
Task: "T012 test_valid_user_profiles_accepted in test_validate_files.py"
```

---

## Implementation Strategy

### MVP First (all three stories are P1 — deliver together)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (core library + Terraform module)
3. Complete Phase 3: US1 tests (happy path)
4. Complete Phase 4: US2 tests (missing fields)
5. Complete Phase 5: US3 tests (empty/missing/corrupt)
6. **STOP and VALIDATE**: `uv run pytest glue_jobs/validation/` — all pass; `terraform validate` clean
7. Demo: validation job rejects bad data, logs structured failures, passes good data

### Incremental Delivery

1. Setup + Foundational → core library ready
2. US1 tests → happy path confirmed
3. US2 tests → failure collection confirmed
4. US3 tests → structural failures confirmed
5. Polish → fmt, scan, outputs wired

---

## Notes

- No TDD inversion needed — spec requests tests covering acceptance scenarios (not pre-failing stubs)
- `awsglue.utils.getResolvedOptions` is pre-installed in Glue runtime but NOT in local Python; mock or stub it in tests (`sys.argv` injection or monkeypatch)
- All S3 calls mocked by moto — no real AWS needed for any test
- `terraform validate` requires `terraform init -backend=false` (already done in Phase 1)
- Commit after each phase checkpoint
