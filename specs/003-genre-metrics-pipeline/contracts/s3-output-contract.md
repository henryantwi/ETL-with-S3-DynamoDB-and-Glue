# Contract: S3 Output — Genre-Day Metrics Parquet

**Interface type**: S3 Parquet partition layout  
**Producer**: `etl-genre-metrics` (AWS Glue PySpark job)  
**Consumers**: Phase 4 DynamoDB writer job (`etl-glue-writer`); downstream reporting systems

---

## Partition Layout

```
s3://<processed-bucket>/output/
└── date=<YYYY-MM-DD>/
    └── genre=<genre-name>/
        └── part-00000-<uuid>.snappy.parquet
```

**Partition keys**: `date` (string, `YYYY-MM-DD`), `genre` (string)  
**Compression**: Snappy  
**Format**: Parquet (Spark-written, schema embedded in footer)

---

## Record Schema

```
root
 |-- date: string (nullable = false)
 |-- genre: string (nullable = false)
 |-- total_plays: long (nullable = false)
 |-- distinct_users: long (nullable = false)
 |-- total_listening_time_seconds: double (nullable = false)
 |-- avg_listening_time_per_user_seconds: double (nullable = false)
 |-- top_3_songs: array (nullable = false)
 |    |-- element: struct (containsNull = false)
 |    |    |-- song_name: string (nullable = false)
 |    |    |-- play_count: long (nullable = false)
 |-- top_5_genres_of_day: array (nullable = false)
 |    |-- element: struct (containsNull = false)
 |    |    |-- genre: string (nullable = false)
 |    |    |-- play_count: long (nullable = false)
```

---

## Lookup Contract

A consumer retrieves all metrics for a `(date, genre)` pair by reading a single partition prefix:

```
s3://<processed-bucket>/output/date=2026-05-25/genre=Pop/
```

No additional filtering or full-table scan is required (SC-002). The partition prefix contains exactly one record for the `(date, genre)` pair.

---

## Invariants

| Invariant | Description |
|-----------|-------------|
| One record per `(date, genre)` | No duplicates; no omissions for any pair observed in enriched data (SC-001) |
| `top_3_songs` length | 1–3 elements; never empty if record exists; no padding to 3 if fewer songs |
| `top_3_songs` order | Descending `play_count`, ascending `song_name` tiebreak |
| `top_5_genres_of_day` length | 1–5 elements; same list for all records sharing the same `date` (SC-006) |
| `top_5_genres_of_day` order | Descending `play_count`, ascending `genre` tiebreak |
| `avg_listening_time_per_user_seconds` | `= total_listening_time_seconds / distinct_users`; always ≥ 0 |
| Atomic visibility | Output for a run appears atomically via staging-prefix swap; no partial run output is ever visible (SC-004) |
| Idempotency | Re-running for the same date overwrites the partition; no stale records from prior runs persist |

---

## Staging Path (Internal — Not for Consumer Use)

```
s3://<processed-bucket>/staging/<run_id>/date=*/genre=*/
```

`run_id` = `date` parameter value (e.g., `2026-05-25`). Staging objects are deleted after successful promotion to `output/`. Consumers MUST NOT read from the staging prefix.

---

## Versioning

This contract is version 1.0. Breaking changes (schema changes, partition key changes) require a MAJOR version bump and constitution amendment per governance rules.
