import json
import sys
import time
import boto3
from pyspark.sql import SparkSession, DataFrame

try:
    from transformations import (
        join_activity_to_catalog,
        compute_genre_metrics,
        compute_top_songs,
        compute_top_genres_per_day,
    )
except ImportError:
    from glue_jobs.genre_metrics.transformations import (
        join_activity_to_catalog,
        compute_genre_metrics,
        compute_top_songs,
        compute_top_genres_per_day,
    )


def _log(record: dict) -> None:
    print(json.dumps(record), flush=True)


def emit_cloudwatch_metrics(cw_client, records_read: int, records_written: int, job_duration_seconds: float) -> None:
    cw_client.put_metric_data(
        Namespace="ETL/GenreMetrics",
        MetricData=[
            {
                "MetricName": "RecordsRead",
                "Value": records_read,
                "Unit": "Count",
                "Dimensions": [{"Name": "JobName", "Value": "etl-genre-metrics"}],
            },
            {
                "MetricName": "RecordsWritten",
                "Value": records_written,
                "Unit": "Count",
                "Dimensions": [{"Name": "JobName", "Value": "etl-genre-metrics"}],
            },
            {
                "MetricName": "JobDurationSeconds",
                "Value": job_duration_seconds,
                "Unit": "Seconds",
                "Dimensions": [{"Name": "JobName", "Value": "etl-genre-metrics"}],
            },
        ],
    )


def write_to_staging(metrics_df: DataFrame, processed_bucket: str, run_date: str) -> None:
    staging_path = f"s3://{processed_bucket}/staging/{run_date}/"
    metrics_df.write.mode("overwrite").partitionBy("date", "genre").parquet(staging_path)


def promote_staging_to_output(s3_client, processed_bucket: str, run_date: str) -> None:
    staging_prefix = f"staging/{run_date}/"
    output_prefix = "output/"

    paginator = s3_client.get_paginator("list_objects_v2")
    staging_objects = []
    for page in paginator.paginate(Bucket=processed_bucket, Prefix=staging_prefix):
        staging_objects.extend(page.get("Contents", []))

    # Clear the output date-partitions this run is about to (re)write so stale
    # part files from prior runs — including any garbage-genre partitions — do
    # not accumulate and get re-loaded into DynamoDB. Idempotent: re-run replaces.
    date_partitions = {
        obj["Key"][len(staging_prefix):].split("/", 1)[0]
        for obj in staging_objects
    }
    for date_part in date_partitions:
        stale_prefix = f"{output_prefix}{date_part}/"
        for page in paginator.paginate(Bucket=processed_bucket, Prefix=stale_prefix):
            for obj in page.get("Contents", []):
                s3_client.delete_object(Bucket=processed_bucket, Key=obj["Key"])

    for obj in staging_objects:
        src_key = obj["Key"]
        dst_key = output_prefix + src_key[len(staging_prefix):]
        s3_client.copy_object(
            Bucket=processed_bucket,
            CopySource={"Bucket": processed_bucket, "Key": src_key},
            Key=dst_key,
        )

    for obj in staging_objects:
        s3_client.delete_object(Bucket=processed_bucket, Key=obj["Key"])


def process_pipeline(
    activity_df: DataFrame,
    catalog_df: DataFrame,
    processed_bucket: str,
    run_date: str,
    s3_client,
    cw_client,
    start_ts: float,
    records_read: int,
) -> None:
    t0 = time.time()
    enriched_df = join_activity_to_catalog(activity_df, catalog_df)
    enriched_count = enriched_df.count()
    _log({"stage": "join_complete", "enriched_count": enriched_count, "elapsed_s": round(time.time() - t0, 3)})

    if enriched_count == 0:
        _log({
            "stage": "join_complete",
            "status": "warn",
            "message": "no_records_produced",
            "elapsed_s": round(time.time() - start_ts, 3),
        })
        emit_cloudwatch_metrics(cw_client, records_read, 0, round(time.time() - start_ts, 3))
        return

    t0 = time.time()
    metrics_df = compute_genre_metrics(enriched_df)
    top_songs_df = compute_top_songs(enriched_df)
    metrics_df = metrics_df.join(top_songs_df, on=["date", "genre"], how="left")
    metrics_df = compute_top_genres_per_day(metrics_df)
    genre_day_count = metrics_df.count()
    _log({"stage": "aggregation_complete", "genre_day_count": genre_day_count, "elapsed_s": round(time.time() - t0, 3)})

    t0 = time.time()
    write_to_staging(metrics_df, processed_bucket, run_date)
    promote_staging_to_output(s3_client, processed_bucket, run_date)
    job_duration = round(time.time() - start_ts, 3)
    _log({
        "stage": "write_complete",
        "records_written": genre_day_count,
        "output_prefix": "output/",
        "elapsed_s": round(time.time() - t0, 3),
    })

    emit_cloudwatch_metrics(cw_client, records_read, genre_day_count, job_duration)


def run_pipeline(spark: SparkSession, args: dict) -> None:
    raw_bucket = args["raw_bucket"]
    processed_bucket = args["processed_bucket"]
    listening_prefix = args["listening_prefix"]
    songs_prefix = args["songs_prefix"]
    run_date = args["run_date"]

    start_ts = time.time()

    t0 = time.time()
    # RFC4180 quoting: source CSV escapes embedded quotes by doubling them ("").
    # Spark's default escape char is backslash, which desyncs columns on rows with
    # quoted commas/quotes (e.g. track_name "Speak Your Mind (""We The People"")"),
    # shifting track_genre onto numeric columns (tempo/valence). escape='"' fixes it.
    _csv = (
        spark.read
        .option("header", "true")
        .option("inferSchema", "true")
        .option("quote", '"')
        .option("escape", '"')
        .option("multiLine", "true")
    )
    activity_df = _csv.csv(f"s3://{raw_bucket}/{listening_prefix}")
    catalog_df = _csv.csv(f"s3://{raw_bucket}/{songs_prefix}")
    records_read = activity_df.count()
    _log({"stage": "read_complete", "records_read": records_read, "elapsed_s": round(time.time() - t0, 3)})

    s3_client = boto3.client("s3")
    cw_client = boto3.client("cloudwatch")

    process_pipeline(
        activity_df=activity_df,
        catalog_df=catalog_df,
        processed_bucket=processed_bucket,
        run_date=run_date,
        s3_client=s3_client,
        cw_client=cw_client,
        start_ts=start_ts,
        records_read=records_read,
    )


def main():
    try:
        from awsglue.utils import getResolvedOptions
        args = getResolvedOptions(
            sys.argv,
            ["raw_bucket", "processed_bucket", "listening_prefix", "songs_prefix", "run_date"],
        )
    except ImportError:
        args = {
            "raw_bucket": sys.argv[1],
            "processed_bucket": sys.argv[2],
            "listening_prefix": sys.argv[3],
            "songs_prefix": sys.argv[4],
            "run_date": sys.argv[5],
        }

    spark = SparkSession.builder.appName("etl-genre-metrics").getOrCreate()
    try:
        run_pipeline(spark, args)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
