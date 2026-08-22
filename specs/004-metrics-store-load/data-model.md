# Phase 1 Data Model: Metrics Store Load

## Entity: `GenreDailyMetric` (DynamoDB item in `MusicKPIs`)

One item per `(genre, date)` pair. Replaced wholesale on each successful run (FR-004).

### Key schema

| Attribute | DDB Type | Role | Format / Constraint |
|-----------|----------|------|---------------------|
| `genre` | String (S) | Partition Key | Verbatim genre identifier from Phase 3 output (UTF-8, ≤ 2048 B) |
| `date` | String (S) | Sort Key | `YYYY-MM-DD`, single canonical TZ fixed upstream (10 B) |

### Access patterns

| Pattern | API | Notes |
|---------|-----|-------|
| All KPIs for genre X on date Y | `GetItem(PK=genre, SK=date)` | Primary lookup |
| All dates for genre X | `Query` on table | Table SK is `date` |
| All genres for date Y | `Query` on GSI `date-index` | GSI PK=`date`, SK=`genre`, projection ALL |

### Attribute schema

| Attribute | DDB Type | Source | Constraint |
|-----------|----------|--------|------------|
| `listen_count` | Number (N) | Phase 3 metric | Integer ≥ 1 (zero-listen rows are omitted — R6) |
| `unique_listener_count` | Number (N) | Phase 3 metric | Integer ≥ 1 |
| `total_listening_time` | Number (N) | Phase 3 metric | Integer seconds ≥ 0 (per spec clarification) |
| `avg_listening_time_per_user` | Number (N) | Phase 3 metric | Decimal seconds ≥ 0 |
| `top_3_songs` | List (L) of Map (M) | Phase 3 ordered list | ≤ 3 entries, ordered by ranking metric desc. Each entry: `{song_id: S, song_name: S, listen_count: N}` |
| `top_5_genres` | List (L) of Map (M) | Phase 3 ordered list (same per date for every genre item that date) | ≤ 5 entries, ordered by ranking metric desc. Each entry: `{genre_id: S, genre_name: S, listen_count: N}` |

### Validation rules

- `listen_count`, `unique_listener_count`, `total_listening_time` are integers (no fractional values written even if numerically equal).
- `avg_listening_time_per_user` is a Decimal (boto3 requires `decimal.Decimal` for `N` fractional values).
- Lists shorter than the cap (3 / 5) are valid; never padded with placeholders (FR-008).
- A record is written only when `listen_count ≥ 1` (R6 — Phase 3 already enforces this).
- Item size MUST be ≤ 400 KB (DynamoDB item limit). Per estimate in research R2, expected size is ~1.2 KB — three orders of magnitude under limit. Writer surfaces a `TransactWriteItems` ValidationException loudly if exceeded (FR-009, spec edge case).

### State transitions

A `GenreDailyMetric` has two observable states:

```
[absent]  ──run includes (genre,date)──►  [present, atomic with run cohort]
[present] ──run includes (genre,date)──►  [present, replaced wholesale]
[present] ──run omits (genre,date)──►    [present, unchanged]    (writer never deletes)
```

Atomic-visibility scope: all items written by one run transition together (R2).

---

## Entity: `PipelineRun` (writer-side logical view)

Spans all `(genre, date)` items written for a single input `run_date`. Not persisted as a separate row — it is the unit of `TransactWriteItems`.

| Attribute | Source | Constraint |
|-----------|--------|------------|
| `run_date` | Glue job argument `--run_date` | `YYYY-MM-DD`; selects S3 partition `output/date=<run_date>/` |
| `record_count` | Count of `(genre, date)` rows read from Parquet | 1 ≤ count ≤ 100 (spec clarification + DynamoDB transaction limit) |
| `start_ts` / `end_ts` | Wall clock at job start/end | Used for `JobDurationSeconds` CloudWatch metric |

### Invariants

- `record_count = 0` → writer logs a structured warn record and exits 0 (no transaction issued, no metrics-store change). Operator alarms still fire if Phase 3 is expected to produce records and produces zero.
- `record_count > 100` → writer fails loudly (FR-009) with a structured error log naming the count. Run-volume bound is part of the architectural contract (spec clarification + R2); exceeding it is an upstream regression to be fixed, not silently chunked.

---

## Read access (Phase 1 design, enforced in IAM)

| Principal | DynamoDB actions | Resource | Notes |
|-----------|------------------|----------|-------|
| `etl-glue-writer-role` | `PutItem`, `TransactWriteItems`, `DescribeTable` | `arn:aws:dynamodb:<region>:<account>:table/MusicKPIs` | Writer only; no read; GSI updates are implicit on Put |
| Consumer roles (named, e.g. `dashboard-reader-role`) | `GetItem`, `Query` | table ARN + `.../index/date-index` | Attached via shared `metrics-reader-policy`; `Scan` is not granted |

No principal — including the writer role — is granted `Scan` or `DeleteItem` or table-admin actions.
