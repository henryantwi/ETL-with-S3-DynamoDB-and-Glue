"""Unit tests for archive_files.py — moto mocks all S3 calls."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from glue_jobs.archive.archive_files import archive_prefix, run_archive

REGION = "eu-west-1"
RAW_BUCKET = "raw-data-test"
ARCHIVE_BUCKET = "archive-test"


def _create_bucket(s3, name: str) -> None:
    s3.create_bucket(
        Bucket=name,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )


def _put(s3, bucket: str, key: str, body: bytes = b"data") -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=body)


def _keys(s3, bucket: str, prefix: str = "") -> list[str]:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return [o["Key"] for o in resp.get("Contents", [])]


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        _create_bucket(client, RAW_BUCKET)
        _create_bucket(client, ARCHIVE_BUCKET)
        yield client


# ---------------------------------------------------------------------------
# archive_prefix tests
# ---------------------------------------------------------------------------


def test_files_moved_to_archive(s3):
    _put(s3, RAW_BUCKET, "listening-activity/file1.csv")
    _put(s3, RAW_BUCKET, "listening-activity/file2.csv")

    count = archive_prefix(s3, RAW_BUCKET, ARCHIVE_BUCKET, "listening-activity/")

    assert count == 2
    assert _keys(s3, ARCHIVE_BUCKET, "listening-activity/") == [
        "listening-activity/file1.csv",
        "listening-activity/file2.csv",
    ]
    assert _keys(s3, RAW_BUCKET, "listening-activity/") == []


def test_empty_prefix_returns_zero(s3):
    count = archive_prefix(s3, RAW_BUCKET, ARCHIVE_BUCKET, "listening-activity/")
    assert count == 0


def test_only_prefix_files_moved(s3):
    _put(s3, RAW_BUCKET, "listening-activity/file.csv")
    _put(s3, RAW_BUCKET, "song-catalog/songs.csv")

    archive_prefix(s3, RAW_BUCKET, ARCHIVE_BUCKET, "listening-activity/")

    assert _keys(s3, RAW_BUCKET, "song-catalog/") == ["song-catalog/songs.csv"]
    assert _keys(s3, RAW_BUCKET, "listening-activity/") == []


# ---------------------------------------------------------------------------
# run_archive tests
# ---------------------------------------------------------------------------


def test_run_archive_moves_all_prefixes(s3):
    _put(s3, RAW_BUCKET, "listening-activity/streams.csv")
    _put(s3, RAW_BUCKET, "song-catalog/songs.csv")
    _put(s3, RAW_BUCKET, "user-profiles/users.csv")

    run_archive(
        s3_client=s3,
        raw_bucket=RAW_BUCKET,
        archive_bucket=ARCHIVE_BUCKET,
        prefixes=["listening-activity/", "song-catalog/", "user-profiles/"],
    )

    assert _keys(s3, RAW_BUCKET) == []
    assert len(_keys(s3, ARCHIVE_BUCKET)) == 3


def test_run_archive_idempotent_on_empty_prefix(s3, capsys):
    run_archive(
        s3_client=s3,
        raw_bucket=RAW_BUCKET,
        archive_bucket=ARCHIVE_BUCKET,
        prefixes=["listening-activity/"],
    )
    captured = capsys.readouterr()
    assert "archive_complete" in captured.out
