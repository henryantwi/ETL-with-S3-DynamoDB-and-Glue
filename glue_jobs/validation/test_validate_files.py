"""Tests for validate_files.py — moto mocks all S3 calls, no real AWS."""

from __future__ import annotations

import pytest
import boto3
from moto import mock_aws

from glue_jobs.validation.validate_files import (
    SCHEMAS,
    FileSchema,
    ValidationResult,
    validate_all,
    validate_file,
)

REGION = "eu-west-1"
BUCKET = "raw-data-test"

LISTENING_KEY = "listening-activity/data.csv"
SONGS_KEY = "song-catalog/data.csv"
USERS_KEY = "user-profiles/data.csv"

VALID_LISTENING = b"user_id,track_id,listen_time\nu001,t001,2026-05-20T10:00:00Z\n"
VALID_SONGS = b"track_id,track_name,artists,track_genre,duration_ms\nt001,Song A,Artist A,Pop,210000\n"
VALID_USERS = b"user_id,user_name,user_country\nu001,johndoe,GH\n"


def _put(s3, key: str, body: bytes) -> None:
    s3.put_object(Bucket=BUCKET, Key=key, Body=body)


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        yield client


# ---------------------------------------------------------------------------
# Phase 3 — US1: Valid files pass through
# ---------------------------------------------------------------------------


def test_all_valid_files_pass(s3):
    _put(s3, LISTENING_KEY, VALID_LISTENING)
    _put(s3, SONGS_KEY, VALID_SONGS)
    _put(s3, USERS_KEY, VALID_USERS)
    results = validate_all(s3, BUCKET)
    failures = [r for r in results if r.status == "FAIL"]
    assert failures == [], f"Expected no failures, got: {failures}"


def test_valid_listening_activity_accepted(s3):
    _put(s3, LISTENING_KEY, VALID_LISTENING)
    schema = SCHEMAS[0]
    result = validate_file(s3, BUCKET, schema)
    assert result.status == "PASS"
    assert result.file_type == "listening-activity"
    assert result.missing_fields == []
    assert result.failure_reason is None


def test_valid_song_catalog_accepted(s3):
    _put(s3, SONGS_KEY, VALID_SONGS)
    schema = SCHEMAS[1]
    result = validate_file(s3, BUCKET, schema)
    assert result.status == "PASS"
    assert result.file_type == "song-catalog"
    assert result.missing_fields == []
    assert result.failure_reason is None


def test_valid_user_profiles_accepted(s3):
    _put(s3, USERS_KEY, VALID_USERS)
    schema = SCHEMAS[2]
    result = validate_file(s3, BUCKET, schema)
    assert result.status == "PASS"
    assert result.file_type == "user-profiles"
    assert result.missing_fields == []
    assert result.failure_reason is None


# ---------------------------------------------------------------------------
# Phase 4 — US2: Missing fields halt the pipeline
# ---------------------------------------------------------------------------


def test_missing_field_in_listening_activity(s3):
    _put(s3, LISTENING_KEY, b"user_id,listen_time\nu001,2026-05-20T10:00:00Z\n")
    schema = SCHEMAS[0]
    result = validate_file(s3, BUCKET, schema)
    assert result.status == "FAIL"
    assert "track_id" in result.missing_fields
    assert result.failure_reason == "field-error"


def test_multiple_missing_fields_in_song_catalog(s3):
    _put(s3, SONGS_KEY, b"track_id,track_name,duration_ms\nt001,Song A,210000\n")
    schema = SCHEMAS[1]
    result = validate_file(s3, BUCKET, schema)
    assert result.status == "FAIL"
    assert "artists" in result.missing_fields
    assert "track_genre" in result.missing_fields
    assert result.failure_reason == "field-error"


def test_one_failure_blocks_all_files(s3):
    # Only upload listening-activity with a missing field; others valid
    _put(s3, LISTENING_KEY, b"user_id,listen_time\nu001,2026-05-20\n")  # missing track_id
    _put(s3, SONGS_KEY, VALID_SONGS)
    _put(s3, USERS_KEY, VALID_USERS)
    results = validate_all(s3, BUCKET)
    failures = [r for r in results if r.status == "FAIL"]
    assert len(failures) >= 1
    # validate_all must return all results (not stop early)
    assert len(results) == 3


def test_multiple_file_failures_collected_together(s3):
    # listening-activity: missing track_id; user-profiles: missing user_country
    _put(s3, LISTENING_KEY, b"user_id,listen_time\nu001,2026-05-20\n")
    _put(s3, SONGS_KEY, VALID_SONGS)
    _put(s3, USERS_KEY, b"user_id,user_name\nu001,johndoe\n")
    results = validate_all(s3, BUCKET)
    failures = [r for r in results if r.status == "FAIL"]
    assert len(failures) == 2
    types = {r.file_type for r in failures}
    assert "listening-activity" in types
    assert "user-profiles" in types


# ---------------------------------------------------------------------------
# Phase 5 — US3: Unreadable, empty, or missing files are rejected
# ---------------------------------------------------------------------------


def test_missing_file_rejected(s3):
    # Do NOT upload anything at listening-activity prefix
    _put(s3, SONGS_KEY, VALID_SONGS)
    _put(s3, USERS_KEY, VALID_USERS)
    schema = SCHEMAS[0]
    result = validate_file(s3, BUCKET, schema)
    assert result.status == "FAIL"
    assert result.failure_reason == "missing"


def test_empty_file_rejected(s3):
    _put(s3, LISTENING_KEY, b"")
    schema = SCHEMAS[0]
    result = validate_file(s3, BUCKET, schema)
    assert result.status == "FAIL"
    assert result.failure_reason == "empty"


def test_unreadable_file_rejected(s3):
    _put(s3, LISTENING_KEY, bytes([0xFF, 0xFE, 0x00, 0x01, 0x80, 0x90]))
    schema = SCHEMAS[0]
    result = validate_file(s3, BUCKET, schema)
    assert result.status == "FAIL"
    assert result.failure_reason == "unreadable"


def test_infrastructure_failure_halts_pipeline(s3, monkeypatch):
    from botocore.exceptions import ClientError

    def _raise(*args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
            "GetObject",
        )

    monkeypatch.setattr(s3, "list_objects_v2", _raise)
    schema = SCHEMAS[0]
    result = validate_file(s3, BUCKET, schema)
    assert result.status == "FAIL"
    assert result.failure_reason == "unreadable"
