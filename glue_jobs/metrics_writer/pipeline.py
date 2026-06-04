import io
import json
import re
import sys
import time
from urllib.parse import unquote

import boto3
import pyarrow.parquet as pq

# DynamoDB TransactWriteItems caps each call at 100 items; a daily run can
# produce more (real data: ~122 genres), so the load is chunked.
TRANSACT_CHUNK = 100

_GENRE_RE = re.compile(r"/genre=([^/]+)/")
_DATE_RE = re.compile(r"/date=([^/]+)/")

try:
    from transformations import build_transact_batch, parquet_rows_to_ddb_items
except ImportError:
    from glue_jobs.metrics_writer.transformations import build_transact_batch, parquet_rows_to_ddb_items


def _log(record: dict) -> None:
    print(json.dumps(record), flush=True)


def _emit_metrics(cw, records_read: int, records_written: int, duration_s: float) -> None:
    cw.put_metric_data(
        Namespace="ETL/MetricsWriter",
        MetricData=[
            {
                "MetricName": "RecordsRead",
                "Value": records_read,
                "Unit": "Count",
                "Dimensions": [{"Name": "JobName", "Value": "etl-metrics-writer"}],
            },
            {
                "MetricName": "RecordsWritten",
                "Value": records_written,
                "Unit": "Count",
                "Dimensions": [{"Name": "JobName", "Value": "etl-metrics-writer"}],
            },
            {
                "MetricName": "JobDurationSeconds",
                "Value": duration_s,
                "Unit": "Seconds",
                "Dimensions": [{"Name": "JobName", "Value": "etl-metrics-writer"}],
            },
        ],
    )


def _read_parquet_from_s3(s3, bucket: str, prefix: str, run_date: str) -> list:
    """Read all Parquet under prefix. Spark's partitionBy("date","genre") strips
    those columns into the key path (e.g. .../date=2024-06-25/genre=hip-hop/...),
    so reconstruct them from the key when absent from the file body. Flat files
    that still carry genre/date as columns are passed through unchanged."""
    paginator = s3.get_paginator("list_objects_v2")
    objects = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".parquet"):
                objects.append(obj)

    # Process oldest-first so that if a partition accumulated stale part files
    # across runs, the newest file wins the (genre, date) key. TransactWriteItems
    # also forbids duplicate keys in one call, so dedup is required, not just tidy.
    objects.sort(key=lambda o: (o["LastModified"], o["Key"]))

    by_key = {}
    for obj in objects:
        key = obj["Key"]
        genre_m = _GENRE_RE.search("/" + key)
        date_m = _DATE_RE.search("/" + key)
        path_genre = unquote(genre_m.group(1)) if genre_m else None
        path_date = unquote(date_m.group(1)) if date_m else run_date
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        table = pq.read_table(io.BytesIO(body))
        for row in table.to_pylist():
            if not row.get("genre") and path_genre is not None:
                row["genre"] = path_genre
            if not row.get("date"):
                row["date"] = path_date
            by_key[(row.get("genre"), row.get("date"))] = row
    return list(by_key.values())


def run_pipeline(args: dict, s3=None, ddb=None, cw=None) -> None:
    processed_bucket = args["processed_bucket"]
    metrics_table = args["metrics_table"]
    run_date = args["run_date"]

    if s3 is None:
        s3 = boto3.client("s3")
    if ddb is None:
        ddb = boto3.client("dynamodb")
    if cw is None:
        cw = boto3.client("cloudwatch")

    start_ts = time.time()

    # Read ALL date partitions, not output/date={run_date}/: the genre job
    # partitions by the DATA's event dates (listen_time), which generally do
    # not equal the pipeline run date — filtering by run_date reads zero rows
    # for any historical upload. DynamoDB upserts are idempotent on
    # (genre, date), so re-loading already-written partitions is harmless.
    prefix = "output/"
    t0 = time.time()
    rows = _read_parquet_from_s3(s3, processed_bucket, prefix, run_date)
    records_read = len(rows)
    _log(
        {
            "level": "INFO",
            "stage": "read_complete",
            "records_read": records_read,
            "prefix": prefix,
            "elapsed_s": round(time.time() - t0, 3),
        }
    )

    if records_read == 0:
        _log(
            {
                "level": "WARN",
                "stage": "no_records",
                "message": "zero records read from Parquet; no transaction issued",
                "run_date": run_date,
            }
        )
        _emit_metrics(cw, 0, 0, round(time.time() - start_ts, 3))
        return

    t0 = time.time()
    items = parquet_rows_to_ddb_items(rows)
    records_mapped = len(items)
    _log(
        {
            "level": "INFO",
            "stage": "transform_complete",
            "records_mapped": records_mapped,
            "elapsed_s": round(time.time() - t0, 3),
        }
    )

    # Chunk into <=100-item transactions (DynamoDB TransactWriteItems limit).
    # Each chunk is atomic; Put on (genre, date) keeps the overall load idempotent.
    t0 = time.time()
    for start in range(0, records_mapped, TRANSACT_CHUNK):
        chunk = items[start : start + TRANSACT_CHUNK]
        transact_batch = build_transact_batch(chunk, metrics_table)
        ddb.transact_write_items(TransactItems=transact_batch)
    duration_s = round(time.time() - start_ts, 3)
    _log(
        {
            "level": "INFO",
            "stage": "write_complete",
            "records_written": records_mapped,
            "table": metrics_table,
            "elapsed_s": round(time.time() - t0, 3),
        }
    )

    _emit_metrics(cw, records_read, records_mapped, duration_s)


def main():
    try:
        from awsglue.utils import getResolvedOptions

        args = getResolvedOptions(
            sys.argv,
            ["processed_bucket", "metrics_table", "run_date"],
        )
    except ImportError:
        args = {
            "processed_bucket": sys.argv[1],
            "metrics_table": sys.argv[2],
            "run_date": sys.argv[3],
        }

    run_pipeline(args)


if __name__ == "__main__":
    main()
