"""Glue Python Shell job: validate CSV headers for all three source file types.

Receives job parameters from Step Functions via getResolvedOptions:
  --raw_bucket       S3 bucket name (raw-data bucket)
  --listening_prefix S3 prefix for listening-activity files
  --songs_prefix     S3 prefix for song-catalog files
  --users_prefix     S3 prefix for user-profiles files

Validates header-only (first 4096 bytes) — never loads full file into memory.
Collects ALL failures before raising so operators see the full picture in one run.
On any failure: logs structured JSON per result, raises ValueError → Step Functions FAIL.
On all pass:    logs structured JSON per result, exits cleanly → Step Functions SUCCESS.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
_logger = logging.getLogger(__name__)


def _log_result(result: "ValidationResult") -> None:
    level = logging.ERROR if result.status == "FAIL" else logging.INFO
    _logger.log(
        level,
        json.dumps(
            {
                "job": "etl-validate-files",
                "file_key": result.file_key,
                "file_type": result.file_type,
                "status": result.status,
                "missing_fields": result.missing_fields,
                "failure_reason": result.failure_reason,
            }
        ),
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FileSchema:
    prefix: str
    file_type: str
    required_fields: frozenset


@dataclass
class ValidationResult:
    file_key: str
    file_type: str
    status: str
    missing_fields: list = field(default_factory=list)
    failure_reason: str | None = None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMAS: list[FileSchema] = [
    FileSchema(
        prefix="listening-activity/",
        file_type="listening-activity",
        required_fields=frozenset({"user_id", "track_id", "listened_at"}),
    ),
    FileSchema(
        prefix="song-catalog/",
        file_type="song-catalog",
        required_fields=frozenset({"track_id", "song_name", "artist_name", "genre", "duration"}),
    ),
    FileSchema(
        prefix="user-profiles/",
        file_type="user-profiles",
        required_fields=frozenset({"user_id", "username", "country"}),
    ),
]


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------


def _find_first_csv(s3_client, bucket: str, prefix: str) -> str | None:
    """Return the key of the first CSV object under prefix, or None if absent."""
    resp = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=10)
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key.endswith(".csv"):
            return key
    return None


def read_csv_header(s3_client, bucket: str, key: str) -> list[str]:
    """Stream first 4096 bytes of an S3 object and return parsed CSV header row.

    Raises:
        KeyError: if the object does not exist (caller maps to 'missing')
        UnicodeDecodeError: if bytes are not valid UTF-8 (caller maps to 'unreadable')
        ValueError: if the object is empty / has no header row
    """
    resp = s3_client.get_object(Bucket=bucket, Key=key, Range="bytes=0-4095")
    raw = resp["Body"].read()
    if not raw.strip():
        raise ValueError("empty")
    text = raw.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("empty")
    return [col.strip() for col in header]


# ---------------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------------


def validate_file(s3_client, bucket: str, schema: FileSchema) -> ValidationResult:
    """Validate a single file type against its schema. Never raises."""
    placeholder_key = schema.prefix + "<pending>"
    try:
        key = _find_first_csv(s3_client, bucket, schema.prefix)
        if key is None:
            return ValidationResult(
                file_key=schema.prefix,
                file_type=schema.file_type,
                status="FAIL",
                failure_reason="missing",
            )
        placeholder_key = key
        header = read_csv_header(s3_client, bucket, key)
        missing = sorted(schema.required_fields - set(header))
        if missing:
            return ValidationResult(
                file_key=key,
                file_type=schema.file_type,
                status="FAIL",
                missing_fields=missing,
                failure_reason="field-error",
            )
        return ValidationResult(
            file_key=key,
            file_type=schema.file_type,
            status="PASS",
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        reason = "missing" if code in ("NoSuchKey", "404") else "unreadable"
        return ValidationResult(
            file_key=placeholder_key,
            file_type=schema.file_type,
            status="FAIL",
            failure_reason=reason,
        )
    except UnicodeDecodeError:
        return ValidationResult(
            file_key=placeholder_key,
            file_type=schema.file_type,
            status="FAIL",
            failure_reason="unreadable",
        )
    except ValueError as exc:
        reason = str(exc) if str(exc) in ("empty",) else "unreadable"
        return ValidationResult(
            file_key=placeholder_key,
            file_type=schema.file_type,
            status="FAIL",
            failure_reason=reason,
        )


def validate_all(s3_client, bucket: str, schemas: list[FileSchema] | None = None) -> list[ValidationResult]:
    """Validate all file types unconditionally. Returns full result list."""
    if schemas is None:
        schemas = SCHEMAS
    return [validate_file(s3_client, bucket, schema) for schema in schemas]


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    # getResolvedOptions is pre-installed in Glue runtime; stub in tests via monkeypatch
    from awsglue.utils import getResolvedOptions  # noqa: PLC0415

    args = getResolvedOptions(
        sys.argv,
        ["raw_bucket", "listening_prefix", "songs_prefix", "users_prefix"],
    )
    bucket = args["raw_bucket"]

    # Build schemas from injected prefixes (allows override without code change)
    schemas = [
        FileSchema(
            prefix=args["listening_prefix"],
            file_type="listening-activity",
            required_fields=SCHEMAS[0].required_fields,
        ),
        FileSchema(
            prefix=args["songs_prefix"],
            file_type="song-catalog",
            required_fields=SCHEMAS[1].required_fields,
        ),
        FileSchema(
            prefix=args["users_prefix"],
            file_type="user-profiles",
            required_fields=SCHEMAS[2].required_fields,
        ),
    ]

    s3 = boto3.client("s3")
    results = validate_all(s3, bucket, schemas)

    for result in results:
        _log_result(result)

    failures = [r for r in results if r.status == "FAIL"]
    if failures:
        summary = "; ".join(
            f"{r.file_type}: {r.failure_reason} {r.missing_fields or ''}"
            for r in failures
        )
        raise ValueError(f"Validation failed for {len(failures)} file(s): {summary}")


if __name__ == "__main__":
    main()
