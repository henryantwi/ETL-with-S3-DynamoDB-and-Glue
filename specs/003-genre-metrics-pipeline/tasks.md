---

description: "Task list for Genre Metrics Pipeline implementation"
---

# Tasks: Genre Metrics Pipeline

**Input**: Design documents from `specs/003-genre-metrics-pipeline/`
**Prerequisites**: plan.md ✅, spec.md ✅, data-model.md ✅, contracts/s3-output-contract.md ✅, research.md ✅, quickstart.md ✅

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US4)

---

## Phase 1: Setup (Terraform Infrastructure)

**Purpose**: Provision the processed-data S3 bucket, etl-glue-transform-role IAM role, and extend the Glue module for PySpark G.1X worker support. No Python source yet.

- [X] T001 Extend `terraform/modules/glue/variables.tf` — add `job_type` (default `"pythonshell"`), `worker_type` (default `"G.1X"`), `num_workers` (default `2`) variables
- [X] T002 [P] Extend `terraform/modules/glue/main.tf` — add conditional `glueetl` command block: when `var.job_type == "glueetl"` use `number_of_workers`, `worker_type`, `glue_version = "4.0"`; keep existing pythonshell path unchanged
- [X] T003 [P] Add `aws_iam_role` and `aws_iam_role_policy` resources for `etl-glue-transform-role` to `terraform/modules/iam/main.tf` — least-privilege: `s3:GetObject`/`s3:ListBucket` on raw bucket, `s3:GetObject`/`s3:PutObject`/`s3:DeleteObject`/`s3:ListBucket` on processed bucket, `logs:*` scoped to own log group, `cloudwatch:PutMetricData` with `Resource: "*"`
- [X] T004 [P] Add `processed_bucket_arn` input variable to `terraform/modules/iam/variables.tf`
- [X] T005 [P] Add `glue_transform_role_arn` output to `terraform/modules/iam/outputs.tf`
- [X] T006 Instantiate `modules/s3` for `processed-data` bucket in `terraform/main.tf` — SSE-S3, all public access blocked, versioning disabled
- [X] T007 Instantiate `modules/iam` for `etl-glue-transform-role` and `modules/glue` for `etl-genre-metrics` job (`job_type = "glueetl"`, `worker_type = "G.1X"`, `num_workers = 2`, `script_location = "s3://<glue-scripts-bucket>/genre_metrics/pipeline.py"`) in `terraform/main.tf`
- [X] T007a [P] Add `aws_s3_object` resource in `terraform/main.tf` to upload `glue_jobs/genre_metrics/pipeline.py` to the glue-scripts S3 bucket at key `genre_metrics/pipeline.py`; the Glue job `command.script_location` in T007 references this object's S3 URI
- [X] T008 [P] Add `processed_bucket_id` and `processed_bucket_arn` outputs to `terraform/outputs.tf`

**Checkpoint**: `terraform validate && terraform plan` shows four new resources — processed S3 bucket, IAM role, Glue PySpark job, glue-scripts S3 object — with no errors.

---

## Phase 2: Foundational (Glue Job File Structure + Spark Session)

**Purpose**: File scaffolding and shared SparkSession fixture that all user-story phases depend on. Must complete before any Python implementation tasks.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T009 Create `glue_jobs/genre_metrics/__init__.py` (empty)
- [X] T010 [P] Create `tests/unit/genre_metrics/__init__.py` (empty) and `tests/unit/genre_metrics/conftest.py` with a module-scoped `SparkSession` fixture using `master("local[2]")` and `appName("genre-metrics-test")`
- [X] T011 [P] Create `glue_jobs/genre_metrics/transformations.py` — module skeleton with imports (`pyspark.sql.functions as F`, `pyspark.sql.Window`) and stub function signatures: `join_activity_to_catalog`, `compute_genre_metrics`, `compute_top_songs`, `compute_top_genres_per_day`
- [X] T012 Create `glue_jobs/genre_metrics/pipeline.py` — Glue `getResolvedOptions` arg parsing for `raw_bucket`, `processed_bucket`, `listening_prefix`, `songs_prefix`, `run_date`; `SparkSession.builder` setup with `appName("etl-genre-metrics")`; main orchestration stub that calls transform functions and emits logs/metrics

**Checkpoint**: `python -c "from glue_jobs.genre_metrics import transformations, pipeline"` runs without ImportError; `pytest tests/unit/genre_metrics/ -v` collects 0 tests with no errors.

---

## Phase 3: User Story 1 — Daily Genre Metrics Computed and Stored (Priority: P1) 🎯 MVP

**Goal**: Read listening-activity and song-catalog Parquet from S3, inner-join on `track_id`, aggregate by `(date, genre)` to produce `total_plays`, `distinct_users`, `total_listening_time_seconds`, `avg_listening_time_per_user_seconds`, and write output. One record per `(date, genre)` pair.

**Independent Test**: Supply two genres × two dates (4 genre-day pairs). Confirm exactly 4 output records, correct aggregate values per scenario 3 of US1 (`total_plays=3`, `distinct_users=2`, avg=total_time/2), and correct handling of null/zero duration (FR-009).

- [X] T013 [P] [US1] Add join and aggregation unit tests to `tests/unit/genre_metrics/test_transformations.py`: `test_join_produces_enriched_events` (inner join on track_id, unmatched rows excluded), `test_compute_genre_metrics_one_record_per_pair` (2 genres × 2 dates = 4 records), `test_aggregate_values_correct` (total_plays, distinct_users, total_listening_time, avg), `test_null_duration_contributes_zero` (null duration_seconds → 0.0 contribution)
- [X] T014 [US1] Implement `join_activity_to_catalog(activity_df, catalog_df)` in `glue_jobs/genre_metrics/transformations.py`: inner join on `track_id`, cast `listened_at` to date as `date` column, `F.coalesce("duration_seconds", F.lit(0.0))`, select `user_id`, `track_id`, `date`, `genre`, `song_name`, `duration_seconds`
- [X] T015 [US1] Implement `compute_genre_metrics(enriched_df)` in `glue_jobs/genre_metrics/transformations.py`: `groupBy("date", "genre")` + `COUNT(*)` as `total_plays`, `COUNT(DISTINCT user_id)` as `distinct_users`, `SUM(duration_seconds)` as `total_listening_time_seconds`, computed `avg_listening_time_per_user_seconds = total_listening_time_seconds / distinct_users`
- [X] T016 [US1] Implement `read_inputs()` in `glue_jobs/genre_metrics/pipeline.py`: read `s3://{raw_bucket}/{listening_prefix}*.parquet` and `s3://{raw_bucket}/{songs_prefix}*.parquet` via `spark.read.parquet()`; call `join_activity_to_catalog` and `compute_genre_metrics`; capture start timestamp for duration tracking
- [X] T017 [US1] Add structured JSON logging at read, join, and aggregation stages in `glue_jobs/genre_metrics/pipeline.py`: emit `{"stage": "read_complete", "records_read": N, "elapsed_s": T}`, `{"stage": "join_complete", "enriched_count": N, "elapsed_s": T}`, `{"stage": "aggregation_complete", "genre_day_count": N, "elapsed_s": T}` to stdout (FR-010)

**Checkpoint**: `pytest tests/unit/genre_metrics/test_transformations.py -v` — join and aggregation tests pass; `compute_genre_metrics` returns exactly one row per `(date, genre)` pair in all test cases.

---

## Phase 4: User Story 4 — Atomic Write: All-or-Nothing Output (Priority: P1)

**Goal**: Write all Parquet output to a staging prefix first, then promote atomically to the final `output/` partition path via boto3 copy+delete. Partial writes are never visible to consumers. Same-date re-runs overwrite cleanly.

**Independent Test**: Simulate mid-write failure (mock boto3 copy to raise after partial copy). Confirm no objects appear in `output/`. Then run successfully and confirm all objects appear in `output/` with correct partition paths.

- [X] T018 [P] [US4] Add staging-swap integration tests to `tests/unit/genre_metrics/test_pipeline_integration.py` (moto S3): `test_staging_write_then_promote` (verify staging objects deleted and output objects created after successful run), `test_failed_promote_leaves_no_output` (mock boto3 copy to raise mid-way; confirm output/ prefix empty), `test_staging_path_uses_run_id` (staging prefix = `staging/{run_date}/`)
- [X] T019 [P] [US4] Add idempotency test to `tests/unit/genre_metrics/test_pipeline_integration.py`: run pipeline twice for same `run_date`; confirm output record count identical, no duplicate Parquet files in output partition
- [X] T020 [US4] Implement `write_to_staging(metrics_df, processed_bucket, run_date)` in `glue_jobs/genre_metrics/pipeline.py`: `df.write.mode("overwrite").partitionBy("date","genre").parquet(f"s3://{processed_bucket}/staging/{run_date}/")`
- [X] T021 [US4] Implement `promote_staging_to_output(s3_client, processed_bucket, run_date)` in `glue_jobs/genre_metrics/pipeline.py`: `list_objects_v2` on `staging/{run_date}/`, copy each object to `output/` prefix (replacing `staging/{run_date}/` with `output/`), delete all staging objects after all copies succeed
- [X] T022 [US4] Add JSON log at write-complete stage in `glue_jobs/genre_metrics/pipeline.py`: `{"stage": "write_complete", "records_written": N, "output_prefix": "output/", "elapsed_s": T}` (FR-010)

**Checkpoint**: `pytest tests/unit/genre_metrics/test_pipeline_integration.py -v` — staging-swap, failure isolation, and idempotency tests all pass.

---

## Phase 5: User Story 2 — Top Songs and Top Genres Identified Per Day (Priority: P2)

**Goal**: Compute the 3 most-played songs within each `(date, genre)` group and the 5 highest-play-count genres per date. Rankings are deterministically ordered and appended to every genre-day metrics record. All records for the same date carry the identical `top_5_genres_of_day` list (SC-006).

**Independent Test**: Supply 5 songs in one genre (play counts 10, 8, 6, 4, 2) and 8 active genres on the same day. Confirm `top_3_songs` has exactly 3 entries ordered descending by play_count; confirm `top_5_genres_of_day` has exactly 5 entries, identical across all genre-day records for that date.

- [X] T023 [P] [US2] Add top-3 songs unit tests to `tests/unit/genre_metrics/test_transformations.py`: `test_top_3_songs_truncated` (5 songs → top 3 stored), `test_top_3_songs_tiebreak_alphabetical` (equal play_count → song_name asc wins), `test_fewer_than_3_songs_preserved` (2 songs in genre → 2-element array, no padding)
- [X] T024 [P] [US2] Add top-5 genres unit tests to `tests/unit/genre_metrics/test_transformations.py`: `test_top_5_genres_truncated` (8 genres → top 5), `test_top_5_genres_tiebreak` (equal play_count → genre asc wins), `test_top_5_genres_same_per_date` (all records for same date have identical top_5_genres_of_day list — SC-006), `test_fewer_than_5_genres_preserved` (3 genres → 3-element array)
- [X] T025 [US2] Implement `compute_top_songs(enriched_df)` in `glue_jobs/genre_metrics/transformations.py`: `groupBy("date","genre","song_name").count()` → `play_count`; `Window.partitionBy("date","genre").orderBy(F.desc("play_count"), F.asc("song_name"))`; `F.row_number() <= 3`; `F.collect_list(F.struct("song_name","play_count"))` → `top_3_songs` array column ordered by window rank
- [X] T026 [US2] Implement `compute_top_genres_per_day(metrics_df)` in `glue_jobs/genre_metrics/transformations.py`: `groupBy("date","genre").agg(F.sum("total_plays"))` → daily totals; `Window.partitionBy("date").orderBy(F.desc("total_plays"), F.asc("genre"))`; `row_number() <= 5`; collect to `top_5_genres_of_day` struct list; broadcast-join result back to metrics_df keyed on `date`
- [X] T027 [US2] Integrate `top_3_songs` and `top_5_genres_of_day` columns into the final genre-day metrics DataFrame in `glue_jobs/genre_metrics/pipeline.py`: call `compute_top_songs` and `compute_top_genres_per_day` after `compute_genre_metrics`; join results before passing to `write_to_staging`

**Checkpoint**: `pytest tests/unit/genre_metrics/test_transformations.py -v -k "top"` — all top-songs and top-genres tests pass; SC-006 (same list per date) verified.

---

## Phase 6: User Story 3 — Empty Join Result Logged Without Failure (Priority: P2)

**Goal**: When the enrichment join produces zero rows, the pipeline logs a structured warning, writes no output, and exits with success. Orchestrators see no error; no retry is triggered.

**Independent Test**: Supply a listening-activity file whose `track_id` values have zero matches in the song catalog. Confirm warning JSON in logs, zero objects written to `output/` or `staging/`, and job exit code 0.

- [X] T028 [P] [US3] Add empty-join tests to `tests/unit/genre_metrics/test_pipeline_integration.py` (moto S3): `test_empty_join_logs_warning` (join produces 0 rows → log contains `{"status": "warn", "message": "no_records_produced"}`), `test_empty_join_writes_no_output` (0 rows → no objects in output/ or staging/ prefix), `test_empty_join_exits_success` (pipeline function returns without raising)
- [X] T029 [US3] Implement empty-join guard in `glue_jobs/genre_metrics/pipeline.py`: after `join_activity_to_catalog`, call `.count()`; if `enriched_count == 0`, log `{"stage": "join_complete", "status": "warn", "message": "no_records_produced", "elapsed_s": T}`, emit CloudWatch metrics with `RecordsWritten=0`, and return early (success) without calling write functions (FR-006)

**Checkpoint**: `pytest tests/unit/genre_metrics/test_pipeline_integration.py -v -k "empty"` — all empty-join tests pass; pipeline exits cleanly with no output.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: CloudWatch metrics emission (FR-011), Terraform formatting, full test suite validation, and quickstart scenario verification.

- [X] T030 [P] Add CloudWatch emission tests to `tests/unit/genre_metrics/test_pipeline_integration.py`: `test_cloudwatch_metrics_emitted` (mocked boto3 cloudwatch; verify `put_metric_data` called with `RecordsRead`, `RecordsWritten`, `JobDurationSeconds` in `ETL/GenreMetrics` namespace, `Dimension JobName=etl-genre-metrics`), `test_cloudwatch_emitted_on_empty_join` (metrics emitted even when enriched_count=0 — FR-011)
- [X] T031 Implement `emit_cloudwatch_metrics(cw_client, records_read, records_written, job_duration_seconds)` in `glue_jobs/genre_metrics/pipeline.py`: `put_metric_data(Namespace="ETL/GenreMetrics", MetricData=[RecordsRead, RecordsWritten, JobDurationSeconds])` with `Dimensions=[{Name: "JobName", Value: "etl-genre-metrics"}]`
- [X] T032 Wire `emit_cloudwatch_metrics()` into the main pipeline flow in `glue_jobs/genre_metrics/pipeline.py`: call at pipeline end on both the happy path and the empty-join early-return path; compute `job_duration_seconds` from start timestamp captured in T016 (FR-011)
- [X] T033 [P] Run `terraform fmt` and `terraform validate` on all modified Terraform files (`terraform/modules/glue/`, `terraform/modules/iam/`, `terraform/main.tf`, `terraform/outputs.tf`); fix any format or validation errors
- [X] T036 [P] Add `aws_cloudwatch_metric_alarm` resource to `terraform/main.tf` for `etl-genre-metrics` failure: namespace `Glue`, metric `glue.driver.aggregate.numFailedTasks`, statistic `Sum`, period `300`, threshold `1`, comparison `GreaterThanOrEqualToThreshold`, dimension `JobName = etl-genre-metrics`; `alarm_actions` references an SNS topic ARN variable (add `sns_alarm_topic_arn` input var to `terraform/variables.tf`)
- [X] T034 [P] Run `pytest tests/unit/genre_metrics/ -v` — all tests must pass; confirm test count ≥ 18 across `test_transformations.py` and `test_pipeline_integration.py`
- [X] T035 Run quickstart.md validation: confirm `pytest tests/unit/genre_metrics/ -v` matches expected scenarios; verify staging-swap produces correct S3 partition paths; verify empty-join scenario exits success per quickstart.md Empty-Join Test section

**Checkpoint**: All 36 tasks complete; `pytest tests/unit/genre_metrics/ -v` green; `terraform validate` clean; CloudWatch alarm for `etl-genre-metrics` confirmed via `aws cloudwatch describe-alarms`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately. Terraform and Python source are independent.
- **Foundational (Phase 2)**: Depends on Phase 1 (file paths reference Terraform outputs). **BLOCKS all user story phases.**
- **US1 (Phase 3)**: Depends on Phase 2 completion. No dependency on US2, US3, US4.
- **US4 (Phase 4)**: Depends on Phase 3 (staging write wraps the metrics DataFrame from US1). US4 requires US1 metrics to exist.
- **US2 (Phase 5)**: Depends on Phase 3 (top-N operates on enriched play events). Independent of US4.
- **US3 (Phase 6)**: Depends on Phase 2 only (empty-join guard in pipeline.py). Can start after Foundational; independent of US1, US2, US4.
- **Polish (Phase 7)**: Depends on all story phases complete.

### User Story Dependencies

- **US1 (P1)**: Unblocked after Foundational
- **US4 (P1)**: Depends on US1 (writes the metrics DataFrame)
- **US2 (P2)**: Unblocked after Foundational; can run in parallel with US4
- **US3 (P2)**: Unblocked after Foundational; can run in parallel with US1, US2, US4

### Within Each User Story

- Test tasks (T013, T018–T019, T023–T024, T028) can be written in parallel with each other
- Implementation tasks follow: join/read → aggregation → write → logging
- Each phase has a Checkpoint to validate independently before proceeding

### Parallel Opportunities

- Phase 1: T002–T005, T008 fully parallel; T006 and T007 sequential (T007 reads T006 bucket output)
- Phase 2: T010–T011 parallel; T012 after T011 (imports from transformations)
- Phase 3 vs. Phase 6: US3 (T028–T029) can be worked in parallel with US1 (T013–T017) — they touch different code paths
- Phase 5: T023 and T024 fully parallel

---

## Parallel Example: Phases 3 + 6 Simultaneously

```bash
# Developer A: Phase 3 (US1 — core metrics)
Task T013: Write join/aggregation tests in test_transformations.py
Task T014: Implement join_activity_to_catalog() in transformations.py
Task T015: Implement compute_genre_metrics() in transformations.py

# Developer B: Phase 6 (US3 — empty join) — same session or parallel agent
Task T028: Write empty-join tests in test_pipeline_integration.py
Task T029: Implement empty-join guard in pipeline.py
```

---

## Implementation Strategy

### MVP First (US1 + US4 — both P1)

1. Complete Phase 1: Setup (Terraform)
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: US1 — core metrics computation and basic write
4. Complete Phase 4: US4 — atomic staging-swap write
5. **STOP and VALIDATE**: Pipeline reads, transforms, and writes atomically
6. Deploy to AWS Glue and run end-to-end against sample data

### Incremental Delivery

1. Setup + Foundational → infrastructure and skeletons ready
2. US1 + US4 → MVP: metrics computed and atomically written to S3 ✅
3. US2 → top-3 songs and top-5 genres added to each record ✅
4. US3 → graceful empty-join handling ✅
5. Polish → CloudWatch metrics, Terraform fmt, full suite green ✅

### Single-Developer Sequence

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7
```

All phases are sequentially completable by one developer; estimated ≤ 30 tasks touched across three source files (`transformations.py`, `pipeline.py`) and four Terraform files.

---

## Notes

- `[P]` tasks touch different files — no conflict on concurrent execution
- US4 depends on US1's DataFrame output; complete US1 first
- Tests in `test_pipeline_integration.py` require `moto` + `boto3`; ensure `pip install moto boto3 pyarrow pyspark pytest` before running
- `transformations.py` has zero AWS calls — pure PySpark; `pipeline.py` owns all AWS calls (S3, CloudWatch, Glue args)
- Commit after each phase Checkpoint to preserve progress
- `terraform validate` must pass before any `terraform apply`
