# Data Upload Guide — ETL Pipeline

How to feed the pipeline, in what order, and what breaks when you get it wrong.

## TL;DR (using this repo's data/ files)

```powershell
$RAW = "raw-data-etl-dev-559050223770"

# 1. Reference data FIRST (these do NOT trigger anything)
aws s3 cp data/songs/songs.csv s3://$RAW/song-catalog/songs.csv
aws s3 cp data/users/users.csv s3://$RAW/user-profiles/users.csv

# 2. Combine the stream files into ONE upload (see why below), LAST — this TRIGGERS the pipeline
Get-Content data/streams/streams1.csv | Set-Content streams_all.csv
Get-Content data/streams/streams2.csv | Select-Object -Skip 1 | Add-Content streams_all.csv
Get-Content data/streams/streams3.csv | Select-Object -Skip 1 | Add-Content streams_all.csv
aws s3 cp streams_all.csv s3://$RAW/listening-activity/streams_all.csv
```

Upload to `listening-activity/` is the ignition key. Everything else must already be in place when you turn it.

**Filenames don't matter — prefixes do.** EventBridge matches the S3 *key
prefix* `listening-activity/`, not any filename. `streams1.csv`,
`streams_all.csv`, `foo.csv` — all trigger equally, as long as the object key
starts with `listening-activity/` and ends `.csv`. Likewise your local folder
names (`data/streams/`, `data/songs/`, `data/users/`) are irrelevant — what
counts is the S3 destination prefix you copy them TO.

**Why combine the 3 stream files?** Each individual upload under
`listening-activity/` fires its own pipeline execution. Uploading
streams1/2/3 separately = 3 concurrent runs racing each other (and each run
processes ALL files under the prefix anyway, so you'd compute the same thing
3×). One combined file = one clean run. The headers are identical, so
concatenation (skipping the repeated header rows) is safe.

---

## Why this order

The raw bucket has S3 → EventBridge notifications enabled. One rule watches it:

```
etl-s3-listening-activity-uploaded
  matches: Object Created, key prefix "listening-activity/"
  target:  Step Functions state machine "etl-pipeline"
```

**Only `listening-activity/` uploads fire the pipeline.** `song-catalog/` and
`user-profiles/` uploads are inert — you can upload those any time, in any
order, hours earlier if you want.

The instant a listening-activity object lands, an execution starts and the
first stage (`etl-validate-files`) checks that **all three** prefixes contain
a valid CSV. So the catalog and profiles must already be there.

## The three file types

| Prefix (exact) | Required header columns | Triggers pipeline? |
|---|---|---|
| `song-catalog/` | `track_id`, `track_name`, `artists`, `track_genre`, `duration_ms` | No |
| `user-profiles/` | `user_id`, `user_name`, `user_country` | No |
| `listening-activity/` | `user_id`, `track_id`, `listen_time` | **Yes** |

Rules that apply to all three:
- File must end in **`.csv`** — validation looks for the first `*.csv` key under each prefix and ignores everything else.
- Extra columns are fine; the required ones just have to be present in the header row.
- Header check reads only the first 4KB — huge files are fine.
- UTF-8 encoding required.
- Quoted fields with embedded commas/quotes (RFC4180 `""` escaping) are handled.

## What happens after the trigger

```
listening.csv lands
  → EventBridge starts etl-pipeline (Step Functions)
    → etl-validate-files     header check on ALL three prefixes
                             any fail → bad file MOVED to rejected/<ts>/<key>, pipeline FAILS
    → etl-genre-metrics      Spark join + daily genre KPIs → processed bucket
    → etl-metrics-writer     load KPIs into DynamoDB MusicKPIs
    → etl-archive-files      MOVE raw files to archive bucket (raw is emptied)
```

Note the last step: **after a successful run, your raw files are gone** —
moved to `archive-etl-dev-559050223770`. A second run needs fresh uploads of
all three files.

## Where data ends up

| Data | Destination | Lifecycle |
|---|---|---|
| Raw inputs (success) | `archive-etl-dev-.../` | 90d → Glacier, 365d → deleted |
| Raw inputs (validation FAIL) | `raw-data-etl-.../rejected/<utc-ts>/<original-key>` | quarantined for inspection; never re-triggers (prefix doesn't match the EventBridge rule) |
| KPI parquet | `processed-data-etl-.../output/` | stays; re-runs overwrite their date partitions |
| KPI records | DynamoDB `MusicKPIs` | stays; idempotent upserts on (genre, date) |

Processed data is **not** archived — it IS the product. Only raw inputs move.

---

## What goes wrong (failure modes)

### 1. Uploading listening-activity first
The pipeline fires immediately, validation finds `song-catalog/` and
`user-profiles/` empty → `ValidationFailed`, execution dies. Your listening
file itself passed its own header check, so it stays in raw (only files that
fail validation are quarantined; "missing" has nothing to move). Your catalog
upload a minute later does nothing (wrong prefix to re-trigger). Recovery:
upload the missing files, then use the manual trigger below — all three are
now in place.

### 2. Wrong prefix spelling
`songs/`, `song_catalog/`, `Song-Catalog/` — all invisible to validation
(prefixes are exact, case-sensitive). Result: "no CSV found" → FAIL.
Must be exactly `song-catalog/`, `user-profiles/`, `listening-activity/`.

### 3. Wrong/missing file extension
`listening.txt`, `listening.CSV` (uppercase) → ignored by the `.csv` suffix
check → validation FAIL. (An upload to `listening-activity/listening.txt`
still *triggers* the pipeline — prefix matches — but validation then can't
find a CSV. Worst of both.)

### 4. Missing required headers
Header row lacking e.g. `track_genre` → FAIL with the missing fields listed
in CloudWatch Logs, and the file is **moved to `rejected/<utc-ts>/<key>`**.
Column order doesn't matter; names do (exact match).

### 5. Empty or non-UTF-8 file
Zero-byte file or non-UTF-8 encoding → validation FAIL → file **moved to
`rejected/`**. Fix the file and upload a fresh copy.

### 6. Multiple listening-activity files at once
**Each object creation fires its own execution.** Upload 5 listening files →
5 concurrent pipeline runs. Not catastrophic (DynamoDB writes are idempotent
on (genre, date) and each run overwrites its output partitions), but it burns
Glue DPU-hours and the runs race each other. Prefer one consolidated
listening CSV per batch; if you must upload many, expect N executions.

### 7. Re-uploading to a prefix mid-run
Same as #6 — a new listening upload during a run starts a second, overlapping
execution. Wait for the current run to finish (~5-10 min) before the next batch.

### 8. Forgetting files were archived
Run 2 fails validation because run 1's archive step emptied the raw bucket.
Always upload a complete fresh set per run.

---

## Verifying a run

```powershell
# Did the pipeline start / finish?
aws stepfunctions list-executions --state-machine-arn arn:aws:states:eu-west-1:559050223770:stateMachine:etl-pipeline --max-results 5

# Why did it fail? (validation details, per-file)
aws logs tail /aws-glue/jobs/output --since 30m
aws logs tail /aws/states/etl-pipeline --since 30m

# Results landed?
aws dynamodb scan --table-name MusicKPIs --max-items 5
```

A `FAILED` execution names the failing stage (`ValidationFailed`,
`TransformFailed`, `LoadFailed`, `ArchiveFailed`) in its error output, and
SNS topic `etl-pipeline-alerts` gets alarm notifications on repeated Glue
job failures.

## Manual trigger (skip the event)

Re-run without re-uploading the listening file — start the state machine
directly with the same input EventBridge would send:

```powershell
aws stepfunctions start-execution --state-machine-arn arn:aws:states:eu-west-1:559050223770:stateMachine:etl-pipeline --input '{"raw_bucket":"raw-data-etl-dev-559050223770","archive_bucket":"archive-etl-dev-559050223770","processed_bucket":"processed-data-etl-dev-559050223770","metrics_table":"MusicKPIs","listening_prefix":"listening-activity/","songs_prefix":"song-catalog/","users_prefix":"user-profiles/","run_date":""}'
```

(Files must still exist under all three raw prefixes — manual trigger skips
the event, not the validation.)
