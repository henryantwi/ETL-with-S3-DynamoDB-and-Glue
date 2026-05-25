# Feature Specification: Genre Metrics Pipeline

**Feature Branch**: `003-genre-metrics-pipeline`  
**Created**: 2026-05-25  
**Status**: Draft  
**Input**: User description: "The pipeline must transform validated raw data into daily genre-level performance metrics."

## Clarifications

### Session 2026-05-25

- Q: What is the output storage backend? → A: S3 Parquet partitioned by (date, genre); atomic write via staging-prefix swap.
- Q: What are the input file formats? → A: Parquet for both listening-activity and song-catalog inputs.
- Q: What is the batch processing SLA (SC-007)? → A: 30 minutes — full run (join + aggregation + write) must complete within 30 min for a representative daily dataset.
- Q: What observability signals are required beyond the empty-join warning? → A: Structured JSON logs + CloudWatch metrics (records read, records written, job duration) for key pipeline stages.
- Q: What is the S3 access control model? → A: IAM role with read-only on input bucket, read-write on output bucket; no public access.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Daily Genre Metrics Computed and Stored (Priority: P1)

A data analyst or downstream reporting system needs to retrieve all performance metrics for a specific genre on a specific date in a single lookup. After the validation stage passes, the pipeline enriches play events with genre and duration information by joining listening activity to the song catalog, then groups and aggregates the data to produce one complete metrics record per genre-per-day.

**Why this priority**: This is the primary output contract. Every other story depends on these aggregations existing. Without this, no downstream consumer can function.

**Independent Test**: Supply validated listening-activity and song-catalog data covering two genres across two days. Confirm that one metrics record exists per genre-per-day combination and that the record is retrievable by (date, genre) without any additional filtering.

**Acceptance Scenarios**:

1. **Given** validated listening-activity and song-catalog files are available, **When** the pipeline runs, **Then** one metrics record is produced for each unique (date, genre) pair observed in the joined data.
2. **Given** a metrics record for (2026-05-25, "Pop") is produced, **When** a downstream system requests that record by date and genre, **Then** it receives the full metrics without scanning or filtering any other records.
3. **Given** three play events on the same day in the same genre by two distinct users, **When** metrics are computed, **Then** `total_plays = 3`, `distinct_users = 2`, `total_listening_time` equals the sum of all three event durations, and `avg_listening_time_per_user` equals `total_listening_time / 2`.

---

### User Story 2 — Top Songs and Top Genres Identified Per Day (Priority: P2)

A content curator or analyst needs to know which songs dominated each genre on a given day, and which genres led overall play volume for that day. The pipeline computes the 3 most-played songs within each genre and the 5 highest-play-count genres across all genres for each day.

**Why this priority**: These rankings are high-value derived insights. They depend on the base aggregations from US1 but deliver distinct business value independently.

**Independent Test**: Supply play data with at least 4 songs in one genre and at least 6 genres active on the same day. Confirm that only the top 3 songs are stored per genre and only the top 5 genres appear in the daily ranking.

**Acceptance Scenarios**:

1. **Given** a genre has 5 songs played on a day with play counts 10, 8, 6, 4, 2, **When** metrics are computed, **Then** only the top 3 songs (play counts 10, 8, 6) are stored in that genre's record, ranked by play count descending.
2. **Given** 8 genres are active on a day, **When** metrics are computed, **Then** the daily top-genres list contains exactly 5 entries, ordered by total play count descending.
3. **Given** two songs in a genre have equal play counts that tie for 3rd place, **When** top-3 songs are selected, **Then** a deterministic tiebreak is applied (e.g., alphabetical by song name) so exactly 3 songs are always stored.

---

### User Story 3 — Empty Join Result Logged Without Failure (Priority: P2)

A data engineer runs the pipeline against a date where no play events match any known song in the catalog (e.g., all `track_id` values are unknown). The pipeline logs a warning that no records were produced for that run, writes no output, and exits successfully.

**Why this priority**: Graceful handling of empty results prevents false alerts and keeps pipeline orchestration clean. An empty run is not an error — failing it would cause unnecessary retries and operator burden.

**Independent Test**: Supply a listening-activity file whose `track_id` values have no matches in the song catalog. Confirm the pipeline logs a warning, writes no output records, and reports a successful exit status.

**Acceptance Scenarios**:

1. **Given** the join between listening activity and song catalog produces zero records, **When** the pipeline completes, **Then** a warning is logged stating no metrics were produced for the run, no output is written, and the job exits with success.
2. **Given** the job exits successfully with an empty result, **When** an orchestrator checks the exit status, **Then** the status indicates success (not failure), so no retry is triggered.

---

### User Story 4 — Atomic Write: All-or-Nothing Output (Priority: P1)

A data engineer or downstream system needs confidence that partial metric sets are never written. If the pipeline fails at any point during computation or writing, no output from that run persists.

**Why this priority**: Partial writes corrupt downstream aggregations and create silent data quality issues. Atomicity is a foundational correctness guarantee alongside validation.

**Independent Test**: Simulate a mid-run failure (e.g., abort after writing half the genre records). Confirm that no output from that run is readable by any downstream system.

**Acceptance Scenarios**:

1. **Given** the pipeline begins writing output and fails mid-write, **When** the failure is detected, **Then** no partially written records from that run are accessible to downstream consumers.
2. **Given** the pipeline completes all computation without error, **When** the write step executes, **Then** all metric records for the run become visible atomically — either all records appear together or none do.

---

### Edge Cases

- What happens when a `track_id` in listening-activity has no match in the song catalog? → That play event is excluded from enrichment; if all events are excluded, US3 (empty-join warning) applies.
- What happens when duration is zero or null for a play event? → The event still counts toward `total_plays` and `distinct_users`; zero duration contributes 0 to `total_listening_time`.
- What happens when only one user listened to a genre on a day? → `avg_listening_time_per_user` equals `total_listening_time / 1`.
- What happens when fewer than 3 songs are played in a genre on a day? → The top-songs list contains however many songs exist (1 or 2); no padding or error.
- What happens when fewer than 5 genres are active on a day? → The daily top-genres list contains however many genres exist; no padding or error.
- What happens when the pipeline is re-run for the same date? → Assumed idempotent: output for that date is overwritten with newly computed values.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST read listening-activity and song-catalog inputs as Parquet files from S3, join them on `track_id`, and produce enriched play events containing genre and duration.
- **FR-002**: System MUST group enriched play events by (date, genre) and compute, for each group: total play count, distinct user count, total listening time, and average listening time per user.
- **FR-003**: System MUST compute the 3 most-played songs (by play count) within each (date, genre) group, ranked descending; ties broken deterministically.
- **FR-004**: System MUST compute the 5 genres with the highest total play count for each date across all genres, ranked descending.
- **FR-005**: System MUST store output as Parquet files on S3 partitioned by `date=<value>/genre=<value>/`, such that a downstream system can retrieve all metrics for a (date, genre) pair by reading a single partition prefix with no additional filtering.
- **FR-006**: System MUST log a warning and produce no output records when the enrichment join yields zero rows; the job MUST still exit successfully.
- **FR-010**: System MUST emit structured JSON logs at key pipeline stages (input read, join complete, aggregation complete, write complete) including record counts and elapsed time.
- **FR-011**: System MUST publish CloudWatch metrics for each run: `RecordsRead`, `RecordsWritten`, and `JobDurationSeconds`; these MUST be emitted regardless of whether output records were produced.
- **FR-007**: System MUST guarantee atomic writes via a staging-prefix swap: all Parquet output for a run is written to a temporary S3 prefix first, then atomically promoted to the final partition path; if the job fails at any point before promotion, no partial output is visible to downstream consumers.
- **FR-008**: Play events whose `track_id` does not match any song-catalog entry MUST be silently excluded from enrichment (not treated as an error).
- **FR-009**: Play events with null or zero duration MUST still be counted toward play count and distinct users; their contribution to listening-time metrics MUST be 0.

### Key Entities

- **Play Event (enriched)**: A single listening record joined with song metadata; attributes: `user_id`, `track_id`, `listened_at` (date extracted), `genre`, `duration_seconds`.
- **Genre-Day Metrics Record**: The primary output unit keyed by (date, genre); attributes: `date`, `genre`, `total_plays`, `distinct_users`, `total_listening_time_seconds`, `avg_listening_time_per_user_seconds`, `top_3_songs` (ordered list of song names with play counts), `top_5_genres_of_day` (ordered list of genre names with play counts, same value for all records on the same day).
- **Song Catalog Entry**: Reference data; attributes: `track_id`, `song_name`, `artist_name`, `genre`, `duration_seconds`.
- **Listening Activity Record**: Raw input; attributes: `user_id`, `track_id`, `listened_at`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every (date, genre) pair present in the enriched data has exactly one corresponding metrics record — no duplicates, no omissions.
- **SC-002**: A downstream consumer retrieves complete metrics for any (date, genre) pair in a single operation, without iterating over unrelated records.
- **SC-003**: When the join produces zero records, the pipeline exits successfully 100% of the time — no false failures on empty inputs.
- **SC-004**: When the pipeline fails mid-write, zero partial records from that run are observable by any downstream consumer.
- **SC-005**: Top-3 song rankings within each genre-day record are correct and deterministically ordered for 100% of records.
- **SC-006**: Top-5 genre daily rankings are consistent across all genre-day records sharing the same date — every record for a given day carries the same top-5 list.
- **SC-007**: The pipeline completes a full run (join + aggregation + write) for a representative daily dataset within **30 minutes** — the project's batch SLA.

## Assumptions

- Validated listening-activity and song-catalog files are the sole inputs, both in Parquet format on S3; user-profile data is not required for genre metrics computation.
- A play event's date is derived from the `listened_at` field; time-zone normalization (if any) is handled upstream before this pipeline stage.
- "Average listening time per user" means total listening time for the genre-day divided by the count of distinct users, not a per-play average.
- Output is idempotent: re-running for the same date overwrites existing records for that date.
- The pipeline runs as a batch job triggered after validation passes; near-real-time or streaming execution is out of scope.
- Song catalog is treated as a static reference snapshot for each run; catalog updates mid-run are not considered.
- Output storage is S3 Parquet partitioned by `date=<value>/genre=<value>/`. "Single lookup" means reading the partition prefix for a given (date, genre) — no secondary index scan or full-table scan required.
- Atomic write is implemented via a staging-prefix swap: write to `s3://<bucket>/staging/<run_id>/`, then rename/copy-delete to `s3://<bucket>/output/date=<value>/genre=<value>/` after all partitions are ready.
- Pipeline executes under an IAM role with read-only access to the input S3 bucket and read-write access to the output S3 bucket; no public bucket access is permitted.
