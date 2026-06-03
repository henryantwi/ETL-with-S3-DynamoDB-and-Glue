import io
from unittest.mock import MagicMock

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from glue_jobs.metrics_writer.pipeline import run_pipeline

TABLE_NAME = "MusicKPIs"
BUCKET = "test-processed"
RUN_DATE = "2024-06-25"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_table():
    ddb = boto3.client("dynamodb", region_name="eu-west-1")
    ddb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "genre", "KeyType": "HASH"},
            {"AttributeName": "date", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "genre", "AttributeType": "S"},
            {"AttributeName": "date", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return ddb


def _make_bucket():
    s3 = boto3.client("s3", region_name="eu-west-1")
    s3.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )
    return s3


def _parquet_bytes(rows: list) -> bytes:
    schema = pa.schema(
        [
            pa.field("genre", pa.string()),
            pa.field("date", pa.string()),
            pa.field("listen_count", pa.int64()),
            pa.field("unique_listener_count", pa.int64()),
            pa.field("total_listening_time", pa.int64()),
            pa.field("avg_listening_time_per_user", pa.float64()),
            pa.field(
                "top_3_songs",
                pa.list_(
                    pa.struct(
                        [
                            pa.field("song_id", pa.string()),
                            pa.field("song_name", pa.string()),
                            pa.field("listen_count", pa.int64()),
                        ]
                    )
                ),
            ),
            pa.field(
                "top_5_genres",
                pa.list_(
                    pa.struct(
                        [
                            pa.field("genre_id", pa.string()),
                            pa.field("genre_name", pa.string()),
                            pa.field("listen_count", pa.int64()),
                        ]
                    )
                ),
            ),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _upload_parquet(s3, rows: list, run_date: str = RUN_DATE, key_suffix: str = "part-0.parquet"):
    key = f"output/date={run_date}/{key_suffix}"
    s3.put_object(Bucket=BUCKET, Key=key, Body=_parquet_bytes(rows))


def _parquet_bytes_no_keys(rows: list) -> bytes:
    """Parquet WITHOUT genre/date columns — mirrors real Spark partitionBy output,
    where the partition columns are encoded in the S3 key path, not the file."""
    schema = pa.schema(
        [
            pa.field("listen_count", pa.int64()),
            pa.field("unique_listener_count", pa.int64()),
            pa.field("total_listening_time", pa.int64()),
            pa.field("avg_listening_time_per_user", pa.float64()),
            pa.field(
                "top_3_songs",
                pa.list_(
                    pa.struct(
                        [
                            pa.field("song_id", pa.string()),
                            pa.field("song_name", pa.string()),
                            pa.field("listen_count", pa.int64()),
                        ]
                    )
                ),
            ),
            pa.field(
                "top_5_genres",
                pa.list_(
                    pa.struct(
                        [
                            pa.field("genre_id", pa.string()),
                            pa.field("genre_name", pa.string()),
                            pa.field("listen_count", pa.int64()),
                        ]
                    )
                ),
            ),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _upload_partitioned(s3, genre: str, run_date: str = RUN_DATE):
    """Write one Hive-partitioned genre file (no genre/date columns in body)."""
    row = {
        "listen_count": 100,
        "unique_listener_count": 50,
        "total_listening_time": 3600,
        "avg_listening_time_per_user": 72.0,
        "top_3_songs": [{"song_id": "t1", "song_name": "S1", "listen_count": 30}],
        "top_5_genres": [{"genre_id": "pop", "genre_name": "Pop", "listen_count": 9800}],
    }
    key = f"output/date={run_date}/genre={genre}/part-0.parquet"
    s3.put_object(Bucket=BUCKET, Key=key, Body=_parquet_bytes_no_keys([row]))


def _fixture_rows(n=5):
    return [
        {
            "genre": f"genre_{i}",
            "date": RUN_DATE,
            "listen_count": 100 + i,
            "unique_listener_count": 50 + i,
            "total_listening_time": 3600 * (i + 1),
            "avg_listening_time_per_user": 72.0 + i,
            "top_3_songs": [
                {"song_id": f"trk_{i}_1", "song_name": f"Song {i} A", "listen_count": 30},
            ],
            "top_5_genres": [
                {"genre_id": "pop", "genre_name": "Pop", "listen_count": 9800},
            ],
        }
        for i in range(n)
    ]


def _args(run_date: str = RUN_DATE):
    return {
        "processed_bucket": BUCKET,
        "metrics_table": TABLE_NAME,
        "run_date": run_date,
    }


def _noop_cw():
    cw = MagicMock()
    cw.put_metric_data = MagicMock()
    return cw


# ---------------------------------------------------------------------------
# T015: US1 — Basic happy-path lookup
# ---------------------------------------------------------------------------


@mock_aws
def test_happy_path_single_item_lookup():
    s3 = _make_bucket()
    ddb = _make_table()

    _upload_parquet(s3, _fixture_rows(5))
    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    resp = ddb.get_item(
        TableName=TABLE_NAME,
        Key={"genre": {"S": "genre_0"}, "date": {"S": RUN_DATE}},
    )
    item = resp.get("Item")
    assert item is not None, "GetItem returned no item"
    for field in (
        "listen_count",
        "unique_listener_count",
        "total_listening_time",
        "avg_listening_time_per_user",
        "top_3_songs",
        "top_5_genres",
    ):
        assert field in item, f"missing field: {field}"


@mock_aws
def test_all_five_genres_written():
    s3 = _make_bucket()
    ddb = _make_table()

    _upload_parquet(s3, _fixture_rows(5))
    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    for i in range(5):
        resp = ddb.get_item(
            TableName=TABLE_NAME,
            Key={"genre": {"S": f"genre_{i}"}, "date": {"S": RUN_DATE}},
        )
        assert resp.get("Item") is not None, f"missing genre_{i}"


@mock_aws
def test_not_found_returns_no_item():
    s3 = _make_bucket()
    ddb = _make_table()

    _upload_parquet(s3, _fixture_rows(5))
    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    resp = ddb.get_item(
        TableName=TABLE_NAME,
        Key={"genre": {"S": "nonexistent"}, "date": {"S": RUN_DATE}},
    )
    assert "Item" not in resp


@mock_aws
def test_zero_input_no_transaction_issued():
    s3 = _make_bucket()
    ddb = _make_table()
    mock_ddb = MagicMock(wraps=ddb)

    _upload_parquet(s3, [])
    run_pipeline(_args(), s3=s3, ddb=mock_ddb, cw=_noop_cw())

    mock_ddb.transact_write_items.assert_not_called()


# ---------------------------------------------------------------------------
# T016: US2 — Idempotent reload
# ---------------------------------------------------------------------------


@mock_aws
def test_idempotent_reload_no_duplicates():
    s3 = _make_bucket()
    ddb = _make_table()

    rows = _fixture_rows(5)
    _upload_parquet(s3, rows)

    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())
    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    result = ddb.scan(TableName=TABLE_NAME)
    assert result["Count"] == 5, f"expected 5 items, got {result['Count']}"


@mock_aws
def test_idempotent_reload_values_reflect_latest_run():
    s3 = _make_bucket()
    ddb = _make_table()

    rows_v1 = _fixture_rows(1)
    rows_v1[0]["listen_count"] = 1000
    _upload_parquet(s3, rows_v1, key_suffix="part-v1.parquet")
    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    # overwrite same S3 key with updated value
    rows_v2 = _fixture_rows(1)
    rows_v2[0]["listen_count"] = 1200
    s3.delete_object(Bucket=BUCKET, Key=f"output/date={RUN_DATE}/part-v1.parquet")
    _upload_parquet(s3, rows_v2, key_suffix="part-v2.parquet")
    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    resp = ddb.get_item(
        TableName=TABLE_NAME,
        Key={"genre": {"S": "genre_0"}, "date": {"S": RUN_DATE}},
    )
    assert resp["Item"]["listen_count"]["N"] == "1200"

    result = ddb.scan(TableName=TABLE_NAME)
    assert result["Count"] == 1


# ---------------------------------------------------------------------------
# T018: US3 — Atomic visibility (rollback on failure)
# ---------------------------------------------------------------------------


@mock_aws
def test_atomicity_rollback_preserves_prior_state():
    s3 = _make_bucket()
    ddb = _make_table()

    prior_rows = _fixture_rows(3)
    _upload_parquet(s3, prior_rows, key_suffix="prior.parquet")
    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    first_item = ddb.get_item(
        TableName=TABLE_NAME,
        Key={"genre": {"S": "genre_0"}, "date": {"S": RUN_DATE}},
    )["Item"]
    prior_listen_count = first_item["listen_count"]["N"]

    new_rows = [dict(r, listen_count=r["listen_count"] + 5000) for r in prior_rows]
    s3.delete_object(Bucket=BUCKET, Key=f"output/date={RUN_DATE}/prior.parquet")
    _upload_parquet(s3, new_rows, key_suffix="new.parquet")

    mock_ddb = MagicMock(wraps=ddb)
    from botocore.exceptions import ClientError

    mock_ddb.transact_write_items.side_effect = ClientError(
        {"Error": {"Code": "TransactionCanceledException", "Message": "simulated"}},
        "TransactWriteItems",
    )

    with pytest.raises(ClientError):
        run_pipeline(_args(), s3=s3, ddb=mock_ddb, cw=_noop_cw())

    after_item = ddb.get_item(
        TableName=TABLE_NAME,
        Key={"genre": {"S": "genre_0"}, "date": {"S": RUN_DATE}},
    )["Item"]
    assert after_item["listen_count"]["N"] == prior_listen_count, (
        "Prior state should be intact after failed transaction"
    )


# ---------------------------------------------------------------------------
# Volume: >100 records load via chunked TransactWriteItems (real data = 122 genres/day)
# ---------------------------------------------------------------------------


@mock_aws
def test_over_100_records_all_written_chunked():
    s3 = _make_bucket()
    ddb = _make_table()

    rows = [
        {
            "genre": f"genre_{i}",
            "date": RUN_DATE,
            "listen_count": 10,
            "unique_listener_count": 5,
            "total_listening_time": 100,
            "avg_listening_time_per_user": 20.0,
            "top_3_songs": [],
            "top_5_genres": [],
        }
        for i in range(122)
    ]
    _upload_parquet(s3, rows)

    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    result = ddb.scan(TableName=TABLE_NAME, Select="COUNT")
    assert result["Count"] == 122, f"expected all 122 written, got {result['Count']}"


# ---------------------------------------------------------------------------
# Hive-partitioned output: genre/date come from the S3 key path, not the file
# ---------------------------------------------------------------------------


@mock_aws
def test_partitioned_output_reconstructs_genre_and_date():
    s3 = _make_bucket()
    ddb = _make_table()

    for g in ("hip-hop", "afrobeat", "r-n-b"):
        _upload_partitioned(s3, g)

    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    for g in ("hip-hop", "afrobeat", "r-n-b"):
        resp = ddb.get_item(
            TableName=TABLE_NAME,
            Key={"genre": {"S": g}, "date": {"S": RUN_DATE}},
        )
        assert resp.get("Item") is not None, f"missing genre={g} from partition path"
        assert resp["Item"]["date"]["S"] == RUN_DATE


@mock_aws
def test_duplicate_partition_files_deduped_newest_wins():
    """A genre partition that accumulated >1 part file across runs must collapse
    to a single (genre, date) item — TransactWriteItems rejects duplicate keys."""
    s3 = _make_bucket()
    ddb = _make_table()

    old = _fixture_rows(1)
    old[0]["listen_count"] = 111
    _upload_parquet(s3, old, key_suffix="genre=genre_0/part-1.parquet")
    new = _fixture_rows(1)
    new[0]["listen_count"] = 999
    _upload_parquet(s3, new, key_suffix="genre=genre_0/part-2.parquet")

    run_pipeline(_args(), s3=s3, ddb=ddb, cw=_noop_cw())

    result = ddb.scan(TableName=TABLE_NAME, Select="COUNT")
    assert result["Count"] == 1, f"expected dedup to 1 item, got {result['Count']}"
    resp = ddb.get_item(
        TableName=TABLE_NAME,
        Key={"genre": {"S": "genre_0"}, "date": {"S": RUN_DATE}},
    )
    assert resp["Item"]["listen_count"]["N"] == "999", "newest part file should win"
