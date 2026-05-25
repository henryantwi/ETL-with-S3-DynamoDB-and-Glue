# Research: Genre Metrics Pipeline

**Phase**: 0 — Outline & Research  
**Branch**: `003-genre-metrics-pipeline`  
**Date**: 2026-05-25

All NEEDS CLARIFICATION items resolved before Phase 1 design.

---

## Decision 1: AWS Glue Version

- **Decision**: AWS Glue 4.0 (`GlueVersion = "4.0"`, Spark 3.3.0, Python 3.10)
- **Rationale**: Latest stable Glue version; better Parquet/Arrow performance via Spark 3.3; `--enable-glue-datacatalog` and `--enable-metrics` flags supported; native Parquet write via `df.write.parquet()` without additional libraries.
- **Alternatives considered**: Glue 3.0 (Spark 3.1) — still supported but older; Glue 2.0 — approaching EOL; local Spark without Glue — no managed scaling.

---

## Decision 2: Top-N Songs (PySpark Window Functions)

- **Decision**: Use PySpark `Window.partitionBy("date", "genre").orderBy(F.desc("play_count"), F.asc("song_name"))` + `F.row_number() <= 3` to select top-3 songs per genre-day.
- **Rationale**: Pure distributed PySpark — no Python UDF, no `.collect()`, no Python-side sorting. Deterministic tiebreak via `asc("song_name")` satisfies spec US2 SC3. `row_number` (not `rank`) guarantees exactly 3 rows even with ties.
- **Alternatives considered**: Python `sorted()` after `.collect()` — does not scale; PySpark `rank()` — can return >3 rows on tie; Python UDF — slower due to serialization overhead.

---

## Decision 3: Top-5 Genres Per Day (Broadcast Pattern)

- **Decision**: Compute daily totals independently (`groupBy("date", "genre").agg(sum("total_plays"))`), rank with `Window.partitionBy("date").orderBy(F.desc("total_plays"), F.asc("genre"))` + `row_number() <= 5`, collect to a small struct, then broadcast-join back to genre-day metrics so every record on the same day carries the same `top_5_genres_of_day` list.
- **Rationale**: SC-006 requires identical top-5 list across all records for the same date. Collecting top-5 per day is a small result (≤5 rows × N dates); broadcast join is efficient. Deterministic tiebreak via `asc("genre")` when play counts are equal.
- **Alternatives considered**: Nested aggregation in single pass — harder to enforce "same list per day" invariant; window function directly on metrics records — double-counts if genre records differ.

---

## Decision 4: Atomic Write via Staging-Prefix Swap

- **Decision**: Two-phase write:
  1. Spark writes all partitions to `s3://<output_bucket>/staging/<run_id>/date=*/genre=*/` using `df.write.mode("overwrite").partitionBy("date","genre").parquet(staging_path)`.
  2. After Spark write succeeds, boto3 iterates all staging objects, copies each to the final `output/` prefix, then deletes the staging objects. If any step fails before deletion completes, staging objects remain but final output is unaffected.
- **Rationale**: S3 has no native atomic rename; this pattern is the standard ETL approach. Spark's `overwrite` mode on the staging prefix ensures the staging area is clean for each run. Final output is only visible once all copies complete.
- **Alternatives considered**: Overwrite `output/` directly — Spark's `overwrite` is not atomic at the partition directory level; partial Parquet files may be visible mid-write. S3 multipart upload — atomic per-file but not per-run.
- **Tiebreak for idempotency**: `run_id` uses `date` parameter (e.g., `2026-05-25`) so re-runs for the same date overwrite the same staging prefix.

---

## Decision 5: CloudWatch Custom Metrics

- **Decision**: Use `boto3.client('cloudwatch').put_metric_data(Namespace='ETL/GenreMetrics', MetricData=[...])` at job end to emit `RecordsRead` (input rows), `RecordsWritten` (output genre-day records), `JobDurationSeconds`. Dimension: `JobName=etl-genre-metrics`.
- **Rationale**: FR-011 requires specific metric names; Glue's built-in Spark metrics are at the executor/stage level, not business-level. boto3 `put_metric_data` is the only way to emit custom business metrics from within the job.
- **IAM note**: `cloudwatch:PutMetricData` does not support resource-level restrictions; the IAM policy must allow `Resource: "*"` for this action only. This is a known AWS limitation, not a constitution violation — documented in Complexity Tracking as N/A since no wildcard action is chosen by design; it is an AWS service constraint.
- **Alternatives considered**: Glue built-in metrics via `--enable-metrics` — emits Spark-level throughput, not FR-011 business metrics; CloudWatch Logs Insights metric filters — adds operational complexity.

---

## Decision 6: Glue Terraform Module Extension (PySpark Support)

- **Decision**: Extend existing `terraform/modules/glue/main.tf` with a conditional block: when `var.job_type == "glueetl"`, use `NumberOfWorkers + WorkerType` instead of `max_capacity`; set `glue_version = "4.0"` for PySpark jobs.
- **Rationale**: Avoids creating a second Glue module; single module handles both Python Shell (feature 002) and PySpark (this feature) job types. `count`/`dynamic` blocks in Terraform `command` block: use `job_type` variable to switch `command.name`.
- **Alternatives considered**: Separate `modules/glue-pyspark/` module — duplication of timeout/retries/arguments logic; hardcode PySpark job in `main.tf` — not reusable.

---

## Decision 7: Processed S3 Bucket

- **Decision**: New S3 bucket `processed-data` instantiated via existing `modules/s3`. SSE-S3 encryption, all public access blocked, versioning disabled (outputs are fully recomputable from raw), no lifecycle (Phase 6 concern).
- **Rationale**: Separate bucket enforces IAM least-privilege — validation role never gets write access to processed data; transform role never gets write access to raw data. Matches constitution's "processed bucket" referenced in Phase 3 and Phase 4.
- **Alternatives considered**: Separate prefix on raw bucket — would require transform role to have PutObject on raw bucket (violates least-privilege); archive bucket — different purpose (cold storage).

---

## Decision 8: Testing Strategy

- **Decision**: `transformations.py` tested with pure PySpark in local mode (`SparkSession.builder.master("local[2]")`); `pipeline.py` integration tested with `moto` to mock S3 (staging swap, CloudWatch). Parquet test fixtures created with `pyarrow.parquet.write_table()`.
- **Rationale**: Matches project pattern (pytest). PySpark local mode requires only `pyspark` pip package; no Glue Docker needed. `moto` covers S3 list/copy/delete operations. Separating transforms from AWS calls enables fast unit tests.
- **Alternatives considered**: AWS Glue Docker image (`amazon/aws-glue-libs`) — slow CI spin-up; `pytest-spark` — extra dependency with same local Spark capability.
