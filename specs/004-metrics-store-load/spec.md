# Feature Specification: Metrics Store Load

**Feature Branch**: `004-metrics-store-load`  
**Created**: 2026-05-25  
**Status**: Draft  
**Input**: User description: "The pipeline must load the computed daily genre metrics into a fast-lookup data store so downstream applications can retrieve all metrics for a specific genre on a specific date instantly, without scanning or filtering. Each record in the store represents one genre on one day and must contain: listen count, unique listener count, total listening time, average listening time per user, the top 3 songs for that genre that day, and the top 5 genres for that day. A downstream application must be able to request all metrics for genre=acoustic on date=2024-06-25 and receive the full record in a single lookup with no additional queries. If a record for the same genre and date already exists, it must be replaced — running the pipeline twice on the same data must not create duplicates or accumulate counts. If the load fails partway through, the partial state must not be visible to readers — either all records for a run are committed or none."

## Clarifications

### Session 2026-05-25

- Q: Shape of `top_3_songs` and `top_5_genres` entries → A: Each entry carries id + display name + ranking metric value (song_id/genre_id, name, metric value such as listen_count or score).
- Q: Unit for `total_listening_time` and `avg_listening_time_per_user` → A: Seconds — `total_listening_time` as integer seconds, `avg_listening_time_per_user` as decimal seconds.
- Q: Zero-listen (genre, date) handling → A: Omit — no record written; lookup returns "not found".
- Q: Read access control on lookup store → A: IAM-restricted reads from named consumer roles/services (least-privilege).
- Q: Expected upper bound of records per pipeline run → A: Tens (≤ 100 genres per day).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Instant Genre-Date Lookup (Priority: P1)

A downstream consumer (analytics dashboard, recommendation service, reporting tool) needs all daily metrics for a single (genre, date) pair without scanning or filtering. The consumer issues one direct lookup keyed on genre + date and receives the full record — including listen count, unique listener count, total listening time, average listening time per user, the top 3 songs for that genre on that day, and the top 5 genres on that day — in a single response.

**Why this priority**: Core value proposition. Without instant lookup the pipeline output is unusable by downstream products. Delivers immediate, demonstrable value as an MVP slice.

**Independent Test**: After a successful pipeline run for a known date, issue a lookup for `genre=acoustic, date=2024-06-25` and verify a single response returns all six metric fields populated. No scan, no filter, no follow-up queries.

**Acceptance Scenarios**:

1. **Given** pipeline ran successfully for date `2024-06-25` and produced metrics for genre `acoustic`, **When** consumer requests record for `(acoustic, 2024-06-25)`, **Then** response contains listen count, unique listener count, total listening time, average listening time per user, top 3 songs, and top 5 genres in one record.
2. **Given** pipeline produced records for N genres on a date, **When** consumer requests one specific genre, **Then** only that genre's record is returned (no scan of others).
3. **Given** no metrics exist for the requested (genre, date), **When** consumer issues a lookup, **Then** the response indicates "not found" without partial data.

---

### User Story 2 - Idempotent Reload (Priority: P1)

The pipeline operator reruns the same job (same input date) — due to retry, manual rerun, or upstream replay. Each re-run must overwrite the prior record for that (genre, date) so counts and lists reflect the latest computation, never duplicates or accumulated totals.

**Why this priority**: Without idempotency, retries corrupt the store. Required for safe operation alongside any orchestrator that may re-trigger jobs.

**Independent Test**: Run the load step twice with the same input. Confirm the count of (genre, date) records in the store equals the count after one run, and metric values equal the latest computed values (not doubled, not stale).

**Acceptance Scenarios**:

1. **Given** a record for `(acoustic, 2024-06-25)` with `listen_count=1000` exists, **When** pipeline re-runs with input that yields `listen_count=1200`, **Then** the stored record shows `listen_count=1200` and there is exactly one record for `(acoustic, 2024-06-25)`.
2. **Given** a successful prior run, **When** the same run is repeated against unchanged input, **Then** every stored value for each (genre, date) is identical to the prior state (no drift, no accumulation).

---

### User Story 3 - Atomic Visibility (Priority: P2)

A consumer reading the store during or after a partial load failure must never observe a half-finished dataset. Either all records produced by a given pipeline run are visible together, or none of them are.

**Why this priority**: Prevents downstream applications from acting on incomplete or inconsistent metric snapshots. Important for correctness but secondary to having the lookup work at all (US1) and idempotency (US2).

**Independent Test**: Simulate a load failure midway through writing records for a run. Verify readers querying any (genre, date) targeted by that run either see all the run's new values or the prior state — never a mix.

**Acceptance Scenarios**:

1. **Given** a pipeline run intends to write records for 20 genres on date `2024-06-25`, **When** the load aborts after 8 records, **Then** no reader can observe any of the 20 new records (prior state preserved) until a successful run completes.
2. **Given** a successful load completes, **When** a reader queries any genre for that date immediately after, **Then** the new values are atomically visible across all 20 genres at once.

---

### Edge Cases

- Genre name contains characters that affect key encoding (spaces, slashes, unicode) — must still produce a deterministic single-lookup key.
- Date with zero listens for a genre — does the (genre, date) record exist with zero counts, or is it omitted? (See Assumptions.)
- Pipeline run produces fewer than 3 songs for a genre or fewer than 5 genres overall — top-N lists must contain whatever is available without padding or error.
- Concurrent pipeline runs targeting the same date — second run must not interleave with the first; outcome must equal one of the two runs in isolation, not a mix.
- Record size exceeds store's per-item limit (very large top-N entries) — load must fail loudly rather than silently truncate.
- Reader requests a future date or a date before pipeline ever ran — must return "not found" cleanly.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST persist exactly one record per `(genre, date)` pair in the fast-lookup store.
- **FR-002**: Each record MUST contain six fields: `listen_count` (integer), `unique_listener_count` (integer), `total_listening_time` (integer, seconds), `avg_listening_time_per_user` (decimal, seconds), `top_3_songs`, `top_5_genres`.
- **FR-003**: System MUST support retrieval of a record by `(genre, date)` in a single direct lookup with no scan, filter, or secondary query.
- **FR-004**: System MUST replace any existing record for the same `(genre, date)` on write — re-running the load with the same input MUST NOT create duplicates or accumulate counts.
- **FR-005**: System MUST ensure that records produced by a single pipeline run become visible to readers atomically — either all records for the run are observable, or none are.
- **FR-006**: On load failure partway through a run, system MUST NOT leave any partially-written records of that run visible to readers; the prior committed state MUST be preserved.
- **FR-007**: System MUST encode the lookup key deterministically from `(genre, date)` so the same inputs always resolve to the same record.
- **FR-008**: `top_3_songs` MUST contain at most 3 entries ordered by the same ranking criterion used in metric computation; `top_5_genres` MUST contain at most 5 entries ordered likewise. Lists shorter than the cap are valid when fewer candidates exist.
- **FR-011**: Each entry in `top_3_songs` MUST include `song_id`, `song_name`, and the ranking metric value used for ordering (e.g., `listen_count`). Each entry in `top_5_genres` MUST include `genre_id`, `genre_name`, and the ranking metric value used for ordering. Consumers MUST be able to render the lists without performing any secondary lookup.
- **FR-012**: Read access to the lookup store MUST be restricted to named consumer principals (IAM-authenticated roles/services). Anonymous or public reads MUST NOT be permitted. Each consumer is granted least-privilege read scope on the metrics store only.
- **FR-009**: System MUST surface load failures to the pipeline operator (visible run status / error) rather than silently dropping records.
- **FR-010**: Lookups for `(genre, date)` pairs that have no record MUST return a clear "not found" response. A `(genre, date)` with zero listens MUST NOT have a record in the store; "not found" is the canonical signal for "no activity that day."

### Key Entities *(include if feature involves data)*

- **GenreDailyMetric**: One record per `(genre, date)`. Holds the six metric fields. Keyed for direct single-lookup access by genre + date. Replaced wholesale on each successful run.
- **PipelineRun**: Logical unit of load. Spans all `(genre, date)` records produced for a given input date. Its outputs become visible atomically as a group.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A downstream consumer retrieves the full metric record for any `(genre, date)` in under 50 ms at the 95th percentile.
- **SC-002**: After two consecutive pipeline runs on identical input, the number of records and every metric value in the store is identical to the state after one run (zero duplicates, zero drift).
- **SC-003**: During a simulated mid-load failure, 100% of reader requests for affected `(genre, date)` pairs return either the complete prior state or the complete new state — never a partial mix.
- **SC-004**: Every record returned contains all six required metric fields in 100% of successful lookups for `(genre, date)` pairs that exist.
- **SC-005**: A pipeline operator can detect a failed load within one run cycle (failure surfaces in run status) in 100% of failure cases.
- **SC-006**: System sustains a per-run write volume of up to 100 `(genre, date)` records and completes the load (including atomic publish) within one pipeline run cycle.

## Assumptions

- Genre identifier and date together form a globally unique business key — no other dimensions (e.g., region, platform) are part of the lookup contract in this feature.
- Date granularity is calendar day in a single canonical time zone fixed upstream (the metric computation stage already commits to one). This feature inherits that choice rather than redefining it.
- A `(genre, date)` with zero listens is omitted from the store (no record written) rather than written with zero-valued fields. Downstream "not found" is the signal for "no activity that day."
- Ranking criteria for `top_3_songs` and `top_5_genres` are defined and computed by the upstream metric computation stage; this feature stores the resulting ordered lists verbatim.
- Atomic visibility is per pipeline run scoped to records that run produced; it does not imply cross-run global snapshots or multi-day transactions.
- Concurrent runs targeting the same date are operationally rare; the pipeline orchestrator is expected to serialize them. The store-level guarantee here is that a single run's writes are atomic, not that arbitrary interleaving is reconciled.
- The fast-lookup store is a managed data store provisioned in the existing project infrastructure; capacity sizing and cost model are out of scope for this spec and will be addressed in the plan.
- Per-run record volume is bounded at ≤ 100 genres per day; the atomic-publish strategy chosen in the plan may exploit this bound (e.g., single batched commit) rather than requiring partitioned staging.
