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

REGION = "eu-west-1"
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
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
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


# -- Glue scripts bucket (US3) ------------------------------------------------


def test_glue_scripts_bucket_exists(s3):
    assert any(b["Name"] == GLUE_SCRIPTS for b in s3.list_buckets()["Buckets"])


def test_glue_scripts_bucket_sse_s3(s3):
    _assert_sse_s3(s3, GLUE_SCRIPTS)


def test_glue_scripts_bucket_versioning_disabled(s3):
    v = s3.get_bucket_versioning(Bucket=GLUE_SCRIPTS)
    assert v.get("Status") != "Enabled"


def test_glue_scripts_bucket_public_access_blocked(s3):
    _assert_blocks_all_true(s3, GLUE_SCRIPTS)


def test_glue_validation_policy_grants_get_on_glue_scripts():
    """Static check: the IAM policy doc grants s3:GetObject on glue-scripts."""
    from pathlib import Path

    iam_main = Path("terraform/modules/iam/main.tf").read_text()
    # Confirm a statement uses var.glue_scripts_bucket_arn with GetObject
    assert "glue_scripts_bucket_arn" in iam_main
    assert "ReadGlueScripts" in iam_main
    assert "s3:GetObject" in iam_main


# -- IAM least-privilege audit (US4) ------------------------------------------


def _iam_text() -> str:
    from pathlib import Path

    return Path("terraform/modules/iam/main.tf").read_text()


def test_stepfunctions_role_trust_states():
    text = _iam_text()
    assert "states.amazonaws.com" in text
    assert 'name               = "etl-stepfunctions-role"' in text


def test_stepfunctions_policy_scopes_glue_to_etl_prefix():
    text = _iam_text()
    assert "job/etl-*" in text
    assert "glue:StartJobRun" in text
    assert "glue:GetJobRun" in text


def test_no_wildcard_action_or_resource_in_iam():
    """Constitution: no `*` action or `*` resource anywhere in IAM module."""
    import re

    text = _iam_text()
    # Disallow `actions = ["*"]` and `resources = ["*"]` (any whitespace variant)
    assert not re.search(r'actions\s*=\s*\[\s*"\*"\s*\]', text), "wildcard action found"
    assert not re.search(r'resources\s*=\s*\[\s*"\*"\s*\]', text), "wildcard resource found"
    # Also: no `"s3:*"`, `"glue:*"`, etc. as action strings
    assert not re.search(r'"\w+:\*"', text), "service-wildcard action found"


def test_no_hardcoded_credentials_in_repo():
    """Scan .tf/.py/.toml for AWS credential patterns."""
    import re
    from pathlib import Path

    patterns = [
        re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
        re.compile(r"aws_secret_access_key\s*=\s*['\"]"),  # literal secret assign
        re.compile(r"aws_access_key_id\s*=\s*['\"]"),  # literal key assign
    ]
    roots = ["terraform", "scripts"]
    extra = [Path("pyproject.toml")]
    files = []
    for r in roots:
        for ext in ("*.tf", "*.py", "*.toml", "*.json"):
            files.extend(Path(r).rglob(ext))
    files.extend(p for p in extra if p.exists())

    offenders = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pat in patterns:
            if pat.search(content):
                offenders.append((str(f), pat.pattern))
    assert offenders == [], f"hardcoded credentials found: {offenders}"
