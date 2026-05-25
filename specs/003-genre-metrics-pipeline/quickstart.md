# Quickstart: Genre Metrics Pipeline

**Branch**: `003-genre-metrics-pipeline`

---

## Prerequisites

- Python 3.10+
- `pip install pyspark pytest moto boto3 pyarrow`
- AWS CLI configured (for Terraform + manual S3 verification)
- Terraform ≥ 1.5 (for infrastructure)

---

## Run Unit Tests (Local — No AWS)

```bash
# From repo root
pytest tests/unit/genre_metrics/ -v
```

Tests cover:
- `test_transformations.py` — PySpark local mode; join logic, aggregations, top-3 songs, top-5 genres, empty-join path, null/zero duration handling
- `test_pipeline_integration.py` — moto S3; staging-prefix swap, idempotent re-run, CloudWatch metric emission (mocked)

---

## Deploy Infrastructure

```bash
cd terraform

# 1. Plan to confirm new resources (processed-data bucket, etl-glue-transform-role, etl-genre-metrics job)
terraform plan

# 2. Apply
terraform apply
```

New resources created:
- `aws_s3_bucket.processed_data` — output bucket (SSE-S3, public access blocked)
- `aws_iam_role.glue_transform` (`etl-glue-transform-role`) — least-privilege role
- `aws_glue_job.glue_transform` (`etl-genre-metrics`) — PySpark G.1X × 2

---

## Upload Job Script to S3

```bash
# Get glue-scripts bucket name from Terraform outputs
SCRIPTS_BUCKET=$(terraform -chdir=terraform output -raw glue_scripts_bucket_id)

aws s3 cp glue_jobs/genre_metrics/pipeline.py s3://$SCRIPTS_BUCKET/genre_metrics_pipeline.py
```

---

## Upload Sample Data

```bash
RAW_BUCKET=$(terraform -chdir=terraform output -raw raw_data_bucket_id)

aws s3 cp data/sample/listening-activity.parquet s3://$RAW_BUCKET/listening-activity/
aws s3 cp data/sample/song-catalog.parquet       s3://$RAW_BUCKET/song-catalog/
```

Sample data requirements for manual testing:
- ≥ 2 genres, ≥ 2 dates, ≥ 4 songs per genre, ≥ 6 genres total (to test top-3 and top-5 limits)

---

## Run Glue Job Manually

```bash
aws glue start-job-run \
  --job-name etl-genre-metrics \
  --arguments '{
    "--raw_bucket": "<raw-bucket-name>",
    "--processed_bucket": "<processed-bucket-name>",
    "--listening_prefix": "listening-activity/",
    "--songs_prefix": "song-catalog/",
    "--run_date": "2026-05-25"
  }'
```

Monitor:
```bash
aws glue get-job-run --job-name etl-genre-metrics --run-id <run-id>
```

---

## Verify Output

```bash
PROCESSED_BUCKET=$(terraform -chdir=terraform output -raw processed_data_bucket_id)

# List partitions
aws s3 ls s3://$PROCESSED_BUCKET/output/ --recursive

# Read a partition (requires pyarrow or awswrangler)
python -c "
import pyarrow.parquet as pq
import pyarrow.fs as fs
s3 = fs.S3FileSystem()
table = pq.read_table('$PROCESSED_BUCKET/output/date=2026-05-25/genre=Pop/', filesystem=s3)
print(table.to_pandas().to_string())
"
```

Expected: One row per `(date, genre)` pair; `top_3_songs` has ≤ 3 entries; `top_5_genres_of_day` has ≤ 5 entries and is identical for all records on the same date.

---

## Verify CloudWatch Metrics

```bash
aws cloudwatch get-metric-statistics \
  --namespace ETL/GenreMetrics \
  --metric-name RecordsWritten \
  --dimensions Name=JobName,Value=etl-genre-metrics \
  --start-time 2026-05-25T00:00:00Z \
  --end-time 2026-05-26T00:00:00Z \
  --period 86400 \
  --statistics Sum
```

---

## Empty-Join Test

Upload a listening-activity file whose `track_id` values have no matches in the song catalog, then run the job. Expected result:
- Job exits with status `SUCCEEDED`
- CloudWatch log contains `"status": "warn", "message": "no_records_produced"`
- No objects written to `output/` prefix
- `RecordsWritten` CloudWatch metric = 0

---

## Idempotency Test

Run the job twice with the same `--run_date`. Expected result:
- Output partition contains the same records after the second run as after the first
- No duplicates; no stale records from run 1 persist alongside run 2 records
