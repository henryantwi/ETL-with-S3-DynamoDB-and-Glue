"""SQS consumer: coalesce a burst of S3 upload events into ONE Step Functions run.

Sits between the EventBridge rule (S3 listening-activity/ uploads) and the
etl-pipeline state machine. Two jobs:

  * Coalesce — the Lambda event source mapping batches a burst of uploads (within
    the batching window) into a single invocation, so N uploads -> one execution.
  * Serialize — at most one etl-pipeline execution RUNNING at a time. A batch that
    arrives while a run is in flight is deferred (its SQS messages have their
    visibility extended) and fires exactly one fresh run once the current finishes.

boto3 is provided by the Lambda runtime; no vendored dependencies.
"""

from __future__ import annotations

import json
import os

import boto3

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
QUEUE_URL = os.environ["QUEUE_URL"]
WAIT_SECONDS = int(os.environ.get("WAIT_SECONDS", "60"))

# Static StartExecution input (the 8 fields the state machine expects).
# run_date="" -> the state machine derives it from the execution start time.
PIPELINE_INPUT = {
    "raw_bucket": os.environ["RAW_BUCKET"],
    "archive_bucket": os.environ["ARCHIVE_BUCKET"],
    "processed_bucket": os.environ["PROCESSED_BUCKET"],
    "metrics_table": os.environ["METRICS_TABLE"],
    "listening_prefix": os.environ.get("LISTENING_PREFIX", "listening-activity/"),
    "songs_prefix": os.environ.get("SONGS_PREFIX", "song-catalog/"),
    "users_prefix": os.environ.get("USERS_PREFIX", "user-profiles/"),
    "run_date": "",
}

sfn = boto3.client("stepfunctions")
sqs = boto3.client("sqs")


def _log(record: dict) -> None:
    print(json.dumps(record), flush=True)


def _has_running_execution() -> bool:
    resp = sfn.list_executions(stateMachineArn=STATE_MACHINE_ARN, statusFilter="RUNNING", maxResults=1)
    return len(resp.get("executions", [])) > 0


def _defer(records: list[dict]) -> None:
    """Push every received message back to invisible for WAIT_SECONDS so it
    reappears and re-checks, without marking it failed (no DLQ pressure)."""
    entries = [
        {"Id": str(i), "ReceiptHandle": r["receiptHandle"], "VisibilityTimeout": WAIT_SECONDS}
        for i, r in enumerate(records)
        if r.get("receiptHandle")
    ]
    for start in range(0, len(entries), 10):  # ChangeMessageVisibilityBatch caps at 10
        batch = entries[start : start + 10]
        if batch:
            sqs.change_message_visibility_batch(QueueUrl=QUEUE_URL, Entries=batch)


def handler(event, _context=None):
    records = event.get("Records", [])
    if not records:
        return {"batchItemFailures": []}

    # Serialization guard: if a run is in flight, defer the whole batch.
    if _has_running_execution():
        _defer(records)
        _log({"event": "deferred", "records": len(records), "wait_seconds": WAIT_SECONDS})
        return {"batchItemFailures": []}

    # Coalesce: N records -> ONE execution.
    try:
        out = sfn.start_execution(stateMachineArn=STATE_MACHINE_ARN, input=json.dumps(PIPELINE_INPUT))
        _log({"event": "started", "coalesced": len(records), "executionArn": out["executionArn"]})
    except Exception as exc:  # noqa: BLE001 — transient (throttle, etc.); let SQS retry the batch
        _log({"event": "start_failed", "error": str(exc), "records": len(records)})
        return {"batchItemFailures": [{"itemIdentifier": r["messageId"]} for r in records]}

    # Success -> empty failures so the event source mapping deletes all messages.
    return {"batchItemFailures": []}
