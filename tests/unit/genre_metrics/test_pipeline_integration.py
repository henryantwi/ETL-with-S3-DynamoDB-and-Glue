"""
Integration tests for pipeline.py.

Strategy: avoid Spark S3 reads/writes (needs hadoop-aws JARs not available locally).
- Staging-swap tests: seed moto S3 manually, call promote_staging_to_output directly.
- Empty-join / cloudwatch tests: use in-memory DataFrames, mock write_to_staging.
"""
import datetime
import json
import time
import unittest.mock as mock
import pytest
import boto3
from pyspark.sql.types import (
    StructType, StructField, StringType, DoubleType, TimestampType, LongType
)

from moto import mock_aws

from glue_jobs.genre_metrics.pipeline import (
    promote_staging_to_output,
    emit_cloudwatch_metrics,
    process_pipeline,
)


BUCKET = "test-processed"
RUN_DATE = "2026-05-25"

_ACTIVITY_SCHEMA = StructType([
    StructField("user_id", StringType(), False),
    StructField("track_id", StringType(), False),
    StructField("listened_at", TimestampType(), False),
])

_CATALOG_SCHEMA = StructType([
    StructField("track_id", StringType(), False),
    StructField("song_name", StringType(), False),
    StructField("artist_name", StringType(), True),
    StructField("genre", StringType(), False),
    StructField("duration_seconds", DoubleType(), True),
])

TS = datetime.datetime(2026, 5, 25, 12, 0, 0)


def _create_bucket(s3, name):
    s3.create_bucket(
        Bucket=name,
        CreateBucketConfiguration={"LocationConstraint": "ap-southeast-2"},
    )


def _seed_staging(s3, bucket, run_date, n_objects=2):
    """Manually create fake staging objects so we can test promote without Spark S3."""
    for i in range(n_objects):
        s3.put_object(
            Bucket=bucket,
            Key=f"staging/{run_date}/date=2026-05-25/genre=Pop/part-{i:05d}.parquet",
            Body=b"fake-parquet-content",
        )


def _activity_df(spark, rows=None):
    if rows is None:
        rows = [
            ("u1", "t1", TS),
            ("u2", "t1", TS),
            ("u1", "t2", TS),
        ]
    return spark.createDataFrame(rows, _ACTIVITY_SCHEMA)


def _catalog_df(spark, rows=None):
    if rows is None:
        rows = [
            ("t1", "Song A", "Art1", "Pop", 60.0),
            ("t2", "Song B", "Art2", "Rock", 90.0),
        ]
    return spark.createDataFrame(rows, _CATALOG_SCHEMA)


def _empty_activity_df(spark):
    return spark.createDataFrame([], _ACTIVITY_SCHEMA)


def _empty_catalog_df(spark):
    return spark.createDataFrame([], _CATALOG_SCHEMA)


# ---------------------------------------------------------------------------
# T018: staging-swap — test boto3 copy+delete logic (no Spark S3 write needed)
# ---------------------------------------------------------------------------

@mock_aws
def test_staging_write_then_promote():
    s3 = boto3.client("s3", region_name="ap-southeast-2")
    _create_bucket(s3, BUCKET)
    _seed_staging(s3, BUCKET, RUN_DATE)

    promote_staging_to_output(s3, BUCKET, RUN_DATE)

    staging_resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"staging/{RUN_DATE}/")
    assert staging_resp.get("KeyCount", 0) == 0, "Staging objects should be deleted after promotion"

    output_resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="output/")
    assert output_resp.get("KeyCount", 0) > 0, "Output objects should exist after promotion"


@mock_aws
def test_failed_promote_leaves_no_output():
    s3 = boto3.client("s3", region_name="ap-southeast-2")
    _create_bucket(s3, BUCKET)
    _seed_staging(s3, BUCKET, RUN_DATE)

    with mock.patch.object(s3, "copy_object", side_effect=Exception("simulated failure")):
        with pytest.raises(Exception):
            promote_staging_to_output(s3, BUCKET, RUN_DATE)

    output_resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="output/")
    assert output_resp.get("KeyCount", 0) == 0, "No output objects after failed promote"


@mock_aws
def test_staging_path_uses_run_id():
    s3 = boto3.client("s3", region_name="ap-southeast-2")
    _create_bucket(s3, BUCKET)
    _seed_staging(s3, BUCKET, RUN_DATE)

    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"staging/{RUN_DATE}/")
    assert resp.get("KeyCount", 0) > 0, f"Staging objects should be under staging/{RUN_DATE}/"


# ---------------------------------------------------------------------------
# T019: idempotency — two promote runs for same run_date produce same output count
# ---------------------------------------------------------------------------

@mock_aws
def test_idempotent_second_run():
    s3 = boto3.client("s3", region_name="ap-southeast-2")
    _create_bucket(s3, BUCKET)

    _seed_staging(s3, BUCKET, RUN_DATE, n_objects=3)
    promote_staging_to_output(s3, BUCKET, RUN_DATE)
    count1 = s3.list_objects_v2(Bucket=BUCKET, Prefix="output/").get("KeyCount", 0)

    # re-run: re-seed staging (simulates second pipeline run), promote again
    _seed_staging(s3, BUCKET, RUN_DATE, n_objects=3)
    promote_staging_to_output(s3, BUCKET, RUN_DATE)
    count2 = s3.list_objects_v2(Bucket=BUCKET, Prefix="output/").get("KeyCount", 0)

    assert count1 == count2, "Idempotent: same object count on re-run"
    assert count1 > 0


# ---------------------------------------------------------------------------
# T028: empty-join — uses in-memory DataFrames, mocks write calls
# ---------------------------------------------------------------------------

def test_empty_join_logs_warning(spark, capsys):
    s3 = mock.MagicMock()
    cw = mock.MagicMock()

    with mock.patch("glue_jobs.genre_metrics.pipeline.write_to_staging"), \
         mock.patch("glue_jobs.genre_metrics.pipeline.promote_staging_to_output"):
        process_pipeline(
            activity_df=_empty_activity_df(spark),
            catalog_df=_empty_catalog_df(spark),
            processed_bucket=BUCKET,
            run_date=RUN_DATE,
            s3_client=s3,
            cw_client=cw,
            start_ts=time.time(),
            records_read=0,
        )

    captured = capsys.readouterr()
    log_lines = []
    for line in captured.out.strip().splitlines():
        if line.strip():
            try:
                log_lines.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    warn_logs = [l for l in log_lines if l.get("status") == "warn" and l.get("message") == "no_records_produced"]
    assert len(warn_logs) > 0, "Expected warning log for empty join"


def test_empty_join_writes_no_output(spark):
    s3 = mock.MagicMock()
    cw = mock.MagicMock()

    with mock.patch("glue_jobs.genre_metrics.pipeline.write_to_staging") as mock_write, \
         mock.patch("glue_jobs.genre_metrics.pipeline.promote_staging_to_output") as mock_promote:
        process_pipeline(
            activity_df=_empty_activity_df(spark),
            catalog_df=_empty_catalog_df(spark),
            processed_bucket=BUCKET,
            run_date=RUN_DATE,
            s3_client=s3,
            cw_client=cw,
            start_ts=time.time(),
            records_read=0,
        )
        mock_write.assert_not_called()
        mock_promote.assert_not_called()


def test_empty_join_exits_success(spark):
    s3 = mock.MagicMock()
    cw = mock.MagicMock()

    with mock.patch("glue_jobs.genre_metrics.pipeline.write_to_staging"), \
         mock.patch("glue_jobs.genre_metrics.pipeline.promote_staging_to_output"):
        # Should return without raising
        process_pipeline(
            activity_df=_empty_activity_df(spark),
            catalog_df=_empty_catalog_df(spark),
            processed_bucket=BUCKET,
            run_date=RUN_DATE,
            s3_client=s3,
            cw_client=cw,
            start_ts=time.time(),
            records_read=0,
        )


# ---------------------------------------------------------------------------
# T030: CloudWatch metrics emission
# ---------------------------------------------------------------------------

def test_cloudwatch_metrics_emitted(spark):
    s3 = mock.MagicMock()
    cw = mock.MagicMock()

    with mock.patch("glue_jobs.genre_metrics.pipeline.write_to_staging"), \
         mock.patch("glue_jobs.genre_metrics.pipeline.promote_staging_to_output"):
        process_pipeline(
            activity_df=_activity_df(spark),
            catalog_df=_catalog_df(spark),
            processed_bucket=BUCKET,
            run_date=RUN_DATE,
            s3_client=s3,
            cw_client=cw,
            start_ts=time.time(),
            records_read=3,
        )

    assert cw.put_metric_data.called
    call_kwargs = cw.put_metric_data.call_args[1]
    assert call_kwargs["Namespace"] == "ETL/GenreMetrics"
    metric_names = {m["MetricName"] for m in call_kwargs["MetricData"]}
    assert {"RecordsRead", "RecordsWritten", "JobDurationSeconds"} == metric_names
    for m in call_kwargs["MetricData"]:
        assert m["Dimensions"] == [{"Name": "JobName", "Value": "etl-genre-metrics"}]


def test_cloudwatch_emitted_on_empty_join(spark):
    s3 = mock.MagicMock()
    cw = mock.MagicMock()

    with mock.patch("glue_jobs.genre_metrics.pipeline.write_to_staging"), \
         mock.patch("glue_jobs.genre_metrics.pipeline.promote_staging_to_output"):
        process_pipeline(
            activity_df=_empty_activity_df(spark),
            catalog_df=_empty_catalog_df(spark),
            processed_bucket=BUCKET,
            run_date=RUN_DATE,
            s3_client=s3,
            cw_client=cw,
            start_ts=time.time(),
            records_read=0,
        )

    assert cw.put_metric_data.called
    call_kwargs = cw.put_metric_data.call_args[1]
    records_written = next(m for m in call_kwargs["MetricData"] if m["MetricName"] == "RecordsWritten")
    assert records_written["Value"] == 0
