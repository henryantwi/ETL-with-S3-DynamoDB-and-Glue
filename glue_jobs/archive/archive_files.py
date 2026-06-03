"""Glue Python Shell job: move processed raw files to the archive bucket.

Copies every object under each source prefix from raw_bucket to archive_bucket
(preserving the key path), then deletes the original from raw_bucket.
S3 has no native move, so this is a copy-then-delete loop.

Expected job arguments (injected by Step Functions):
  --raw_bucket        : source bucket name
  --archive_bucket    : destination bucket name
  --listening_prefix  : e.g. listening-activity/
  --songs_prefix      : e.g. song-catalog/
  --users_prefix      : e.g. user-profiles/
"""

from __future__ import annotations

import json
import logging
import sys

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


def _log(record: dict) -> None:
    print(json.dumps(record), flush=True)


# ---------------------------------------------------------------------------
# Core archival logic (importable for unit tests)
# ---------------------------------------------------------------------------


def archive_prefix(
    s3_client,
    raw_bucket: str,
    archive_bucket: str,
    prefix: str,
) -> int:
    """Copy all objects under *prefix* from raw_bucket to archive_bucket, then delete.

    Returns the number of files archived.
    Raises ClientError on the first S3 failure (lets Step Functions catch and fail the run).
    """
    paginator = s3_client.get_paginator("list_objects_v2")
    archived_count = 0

    for page in paginator.paginate(Bucket=raw_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            try:
                s3_client.copy_object(
                    Bucket=archive_bucket,
                    CopySource={"Bucket": raw_bucket, "Key": key},
                    Key=key,
                )
                s3_client.delete_object(Bucket=raw_bucket, Key=key)
                archived_count += 1
                _log(
                    {
                        "event": "file_archived",
                        "src_bucket": raw_bucket,
                        "dst_bucket": archive_bucket,
                        "key": key,
                    }
                )
            except ClientError as exc:
                _log(
                    {
                        "event": "archive_error",
                        "key": key,
                        "error": str(exc),
                    }
                )
                raise

    return archived_count


def run_archive(
    s3_client,
    raw_bucket: str,
    archive_bucket: str,
    prefixes: list[str],
) -> None:
    """Archive all files under each prefix and emit a summary log."""
    total = 0
    for prefix in prefixes:
        count = archive_prefix(s3_client, raw_bucket, archive_bucket, prefix)
        _log(
            {
                "event": "prefix_complete",
                "prefix": prefix,
                "files_archived": count,
            }
        )
        total += count

    _log(
        {
            "event": "archive_complete",
            "raw_bucket": raw_bucket,
            "archive_bucket": archive_bucket,
            "total_files_archived": total,
        }
    )


# ---------------------------------------------------------------------------
# Glue entry point
# ---------------------------------------------------------------------------


def main() -> None:
    from awsglue.utils import getResolvedOptions  # noqa: PLC0415  (Glue runtime only)

    args = getResolvedOptions(
        sys.argv,
        [
            "raw_bucket",
            "archive_bucket",
            "listening_prefix",
            "songs_prefix",
            "users_prefix",
        ],
    )

    s3 = boto3.client("s3")
    run_archive(
        s3_client=s3,
        raw_bucket=args["raw_bucket"],
        archive_bucket=args["archive_bucket"],
        prefixes=[
            args["listening_prefix"],
            args["songs_prefix"],
            args["users_prefix"],
        ],
    )


if __name__ == "__main__":
    main()
