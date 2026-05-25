# Data Model: Genre Metrics Pipeline

**Phase**: 1 — Design & Contracts  
**Branch**: `003-genre-metrics-pipeline`  
**Date**: 2026-05-25

---

## Input Entities

### ListeningActivityRecord (raw input, Parquet)

| Field | Type | Nullable | Notes |
|-------|------|----------|-------|
| `user_id` | string | NO | Unique listener identifier |
| `track_id` | string | NO | Join key to SongCatalogEntry |
| `listened_at` | timestamp / string | NO | Date extracted as `listened_at::date` → `date` field |

**S3 location**: `s3://<raw-bucket>/listening-activity/*.parquet`  
**Validation**: Pre-validated by feature 002 (columns present, non-null); this job treats absent `track_id` matches as silent exclusions (FR-008), not errors.

---

### SongCatalogEntry (reference data, Parquet)

| Field | Type | Nullable | Notes |
|-------|------|----------|-------|
| `track_id` | string | NO | Join key |
| `song_name` | string | NO | Used in `top_3_songs` list |
| `artist_name` | string | YES | Carried through for context; not used in aggregations |
| `genre` | string | NO | Groups play events |
| `duration_seconds` | double | YES | Null/zero → contributes 0 to listening-time metrics (FR-009) |

**S3 location**: `s3://<raw-bucket>/song-catalog/*.parquet`  
**Treatment**: Static snapshot for the run. Catalog updates mid-run are out of scope.

---

## Intermediate Entity

### EnrichedPlayEvent (in-memory, not persisted)

Result of inner join on `track_id`:

| Field | Type | Source |
|-------|------|--------|
| `user_id` | string | ListeningActivityRecord |
| `track_id` | string | ListeningActivityRecord |
| `date` | date | cast(listened_at as date) |
| `genre` | string | SongCatalogEntry |
| `song_name` | string | SongCatalogEntry |
| `duration_seconds` | double | SongCatalogEntry; coalesced to 0.0 if null |

**Join type**: Inner join on `track_id`. Unmatched listening events silently excluded (FR-008). If result is empty → US3 (log warning, no output, success exit).

---

## Output Entity

### GenreDayMetricsRecord (primary output, Parquet)

One record per unique `(date, genre)` pair.

| Field | Type | Nullable | Derivation |
|-------|------|----------|------------|
| `date` | string `YYYY-MM-DD` | NO | Partition key |
| `genre` | string | NO | Partition key |
| `total_plays` | long | NO | `COUNT(*)` of EnrichedPlayEvents per (date, genre) |
| `distinct_users` | long | NO | `COUNT(DISTINCT user_id)` per (date, genre) |
| `total_listening_time_seconds` | double | NO | `SUM(duration_seconds)` per (date, genre); zero-duration events contribute 0 |
| `avg_listening_time_per_user_seconds` | double | NO | `total_listening_time_seconds / distinct_users`; always ≥ 0 |
| `top_3_songs` | array\<struct\<song_name:string, play_count:long\>\> | NO | Top 3 by `play_count desc, song_name asc` within (date, genre); fewer than 3 if genre has <3 songs (no padding) |
| `top_5_genres_of_day` | array\<struct\<genre:string, play_count:long\>\> | NO | Top 5 genres by total plays for the date; same value for all records on the same date (SC-006); fewer than 5 if <5 genres active |

**S3 location (staging)**: `s3://<processed-bucket>/staging/<run_id>/date=<value>/genre=<value>/`  
**S3 location (final)**: `s3://<processed-bucket>/output/date=<value>/genre=<value>/`  
**Format**: Parquet, snappy compression  
**Partitioning**: `date`, `genre` (Hive-style partition directories)

---

## Validation Rules

| Rule | Enforcement |
|------|------------|
| Zero or null `duration_seconds` → contributes 0 to time metrics | `F.coalesce("duration_seconds", F.lit(0.0))` before aggregation |
| Empty join result → warn + success exit | Post-join count check; `if enriched_count == 0: log warning; sys.exit(0)` |
| Top-3 songs: exactly ≤ 3 per (date, genre) | `row_number() <= 3` window filter |
| Top-5 genres: exactly ≤ 5 per date | `row_number() <= 5` window filter on daily totals |
| Tiebreak top-3 songs: deterministic | `orderBy(desc("play_count"), asc("song_name"))` |
| Tiebreak top-5 genres: deterministic | `orderBy(desc("total_plays"), asc("genre"))` |
| Re-run same date: idempotent overwrite | Staging prefix includes `run_id = date`; Spark `mode("overwrite")` on staging; boto3 swap overwrites final output |

---

## State Transitions

```
RAW PARQUET (S3 raw-data)
    │
    ▼ inner join on track_id
ENRICHED_PLAY_EVENTS (Spark in-memory)
    │
    ├── count == 0 ──► LOG WARNING ──► EXIT SUCCESS (no output written)
    │
    ▼ groupBy(date, genre) + window top-N
GENRE_DAY_METRICS (Spark DataFrame)
    │
    ▼ write.parquet(staging_path)
STAGING PARQUET (S3 processed-data/staging/<run_id>/)
    │
    ▼ boto3 copy + delete
FINAL PARQUET (S3 processed-data/output/date=*/genre=*/)
```
