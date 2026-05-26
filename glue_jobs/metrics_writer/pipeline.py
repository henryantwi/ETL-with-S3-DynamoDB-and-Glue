import io
import json
import sys
import time

import boto3
import pyarrow.parquet as pq

try:
    from transformations import parquet_rows_to_ddb_items, build_transact_batch
except ImportError:
    from glue_jobs.metrics_writer.transformations import parquet_rows_to_ddb_items, build_transact_batch


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


def _read_parquet_from_s3(s3, bucket: str, prefix: str) -> list:
    paginator = s3.get_paginator("list_objects_v2")
    rows = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".parquet"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            table = pq.read_table(io.BytesIO(body))
            rows.extend(table.to_pylist())
    return rows


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

    prefix = f"output/date={run_date}/"
    t0 = time.time()
    rows = _read_parquet_from_s3(s3, processed_bucket, prefix)
    records_read = len(rows)
    _log({
        "level": "INFO",
        "stage": "read_complete",
        "records_read": records_read,
        "prefix": prefix,
        "elapsed_s": round(time.time() - t0, 3),
    })

    if records_read == 0:
        _log({
            "level": "WARN",
            "stage": "no_records",
            "message": "zero records read from Parquet; no transaction issued",
            "run_date": run_date,
        })
        _emit_metrics(cw, 0, 0, round(time.time() - start_ts, 3))
        return

    t0 = time.time()
    items = parquet_rows_to_ddb_items(rows)
    transact_batch = build_transact_batch(items, metrics_table)
    records_mapped = len(items)
    _log({
        "level": "INFO",
        "stage": "transform_complete",
        "records_mapped": records_mapped,
        "elapsed_s": round(time.time() - t0, 3),
    })

    t0 = time.time()
    ddb.transact_write_items(TransactItems=transact_batch)
    duration_s = round(time.time() - start_ts, 3)
    _log({
        "level": "INFO",
        "stage": "write_complete",
        "records_written": records_mapped,
        "table": metrics_table,
        "elapsed_s": round(time.time() - t0, 3),
    })

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
            "metrics_table":    sys.argv[2],
            "run_date":         sys.argv[3],
        }

    run_pipeline(args)


if __name__ == "__main__":
    main()
