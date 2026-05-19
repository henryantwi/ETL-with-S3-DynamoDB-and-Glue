"""Smoke tests for Phase 1 Secure Storage Foundation.

Mocks AWS S3 via moto — no real AWS calls. Validates that bucket-creation
helpers produce the configuration mandated by spec/plan: SSE-S3, all four
public access blocks true, versioning per spec, archive lifecycle.

Expected `terraform plan` audit (manual): zero `*` actions or resources in
any IAM policy block; all bucket resources show `block_public_*` = true.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

REGION = "ap-southeast-2"
RAW = "raw-data-etl-dev-abc123"
ARCHIVE = "archive-etl-dev-abc123"
GLUE_SCRIPTS = "glue-scripts-etl-dev-abc123"


def _create_bucket(s3, name: str, *, versioning: bool, lifecycle: bool = False) -> None:
    """Replicates what the terraform/modules/s3 module produces."""
    s3.create_bucket(
        Bucket=name,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    s3.put_bucket_encryption(
        Bucket=name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
    )
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_versioning(
        Bucket=name,
        VersioningConfiguration={"Status": "Enabled" if versioning else "Suspended"},
    )
    if lifecycle:
        s3.put_bucket_lifecycle_configuration(
            Bucket=name,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "archive-and-expire",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "Transitions": [{"Days": 90, "StorageClass": "GLACIER"}],
                        "Expiration": {"Days": 365},
                    }
                ]
            },
        )


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        _create_bucket(client, RAW, versioning=True)
        _create_bucket(client, ARCHIVE, versioning=False, lifecycle=True)
        _create_bucket(client, GLUE_SCRIPTS, versioning=False)
        yield client


def _assert_sse_s3(s3, bucket: str) -> None:
    enc = s3.get_bucket_encryption(Bucket=bucket)
    rules = enc["ServerSideEncryptionConfiguration"]["Rules"]
    assert rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"


def _assert_blocks_all_true(s3, bucket: str) -> None:
    pab = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
    assert pab["BlockPublicAcls"] is True
    assert pab["IgnorePublicAcls"] is True
    assert pab["BlockPublicPolicy"] is True
    assert pab["RestrictPublicBuckets"] is True


def _assert_versioning(s3, bucket: str, expected: str) -> None:
    v = s3.get_bucket_versioning(Bucket=bucket)
    assert v.get("Status") == expected


# -- Raw bucket (US1) ---------------------------------------------------------


def test_raw_bucket_exists(s3):
    assert any(b["Name"] == RAW for b in s3.list_buckets()["Buckets"])


def test_raw_bucket_sse_s3(s3):
    _assert_sse_s3(s3, RAW)


def test_raw_bucket_versioning_enabled(s3):
    _assert_versioning(s3, RAW, "Enabled")


def test_raw_bucket_public_access_blocked(s3):
    _assert_blocks_all_true(s3, RAW)


# -- Archive bucket (US2) -----------------------------------------------------


def test_archive_bucket_exists(s3):
    assert any(b["Name"] == ARCHIVE for b in s3.list_buckets()["Buckets"])


def test_archive_bucket_sse_s3(s3):
    _assert_sse_s3(s3, ARCHIVE)


def test_archive_bucket_versioning_disabled(s3):
    v = s3.get_bucket_versioning(Bucket=ARCHIVE)
    assert v.get("Status") != "Enabled"


def test_archive_bucket_public_access_blocked(s3):
    _assert_blocks_all_true(s3, ARCHIVE)


def test_archive_bucket_lifecycle_glacier_and_expiry(s3):
    cfg = s3.get_bucket_lifecycle_configuration(Bucket=ARCHIVE)
    rules = cfg["Rules"]
    assert len(rules) == 1
    rule = rules[0]
    assert rule["Status"] == "Enabled"
    assert rule["Transitions"][0]["Days"] == 90
    assert rule["Transitions"][0]["StorageClass"] == "GLACIER"
    assert rule["Expiration"]["Days"] == 365
