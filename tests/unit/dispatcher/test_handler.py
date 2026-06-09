"""Unit tests for the pipeline-dispatcher Lambda — boto3 clients monkeypatched."""

from __future__ import annotations

import importlib

import pytest

REQUIRED_ENV = {
    "STATE_MACHINE_ARN": "arn:aws:states:eu-west-1:123456789012:stateMachine:etl-pipeline",
    "QUEUE_URL": "https://sqs.eu-west-1.amazonaws.com/123456789012/etl-pipeline-dispatch",
    "RAW_BUCKET": "raw-data-etl-dev-x",
    "ARCHIVE_BUCKET": "archive-etl-dev-x",
    "PROCESSED_BUCKET": "processed-data-etl-dev-x",
    "METRICS_TABLE": "MusicKPIs",
    "WAIT_SECONDS": "60",
}


@pytest.fixture
def handler(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    import handler as h

    importlib.reload(h)
    return h


def _event(n: int) -> dict:
    return {"Records": [{"messageId": f"m{i}", "receiptHandle": f"rh{i}", "body": "{}"} for i in range(n)]}


def test_no_running_starts_once_for_n_messages(handler, monkeypatch):
    calls = {"start": 0}
    monkeypatch.setattr(handler.sfn, "list_executions", lambda **k: {"executions": []})

    def _start(**k):
        calls["start"] += 1
        assert k["stateMachineArn"] == REQUIRED_ENV["STATE_MACHINE_ARN"]
        return {"executionArn": "arn:aws:states:eu-west-1:123456789012:execution:etl-pipeline:run1"}

    monkeypatch.setattr(handler.sfn, "start_execution", _start)
    resp = handler.handler(_event(3))
    assert calls["start"] == 1
    assert resp == {"batchItemFailures": []}


def test_running_defers_and_does_not_start(handler, monkeypatch):
    calls = {"start": 0, "extend": 0}
    monkeypatch.setattr(handler.sfn, "list_executions", lambda **k: {"executions": [{"executionArn": "x"}]})
    monkeypatch.setattr(handler.sfn, "start_execution", lambda **k: calls.__setitem__("start", calls["start"] + 1))
    monkeypatch.setattr(
        handler.sqs,
        "change_message_visibility_batch",
        lambda **k: calls.__setitem__("extend", calls["extend"] + 1),
    )
    resp = handler.handler(_event(3))
    assert calls["start"] == 0
    assert calls["extend"] == 1
    assert resp == {"batchItemFailures": []}


def test_start_failure_returns_batch_item_failures(handler, monkeypatch):
    monkeypatch.setattr(handler.sfn, "list_executions", lambda **k: {"executions": []})

    def _boom(**k):
        raise RuntimeError("ThrottlingException")

    monkeypatch.setattr(handler.sfn, "start_execution", _boom)
    resp = handler.handler(_event(3))
    ids = {f["itemIdentifier"] for f in resp["batchItemFailures"]}
    assert ids == {"m0", "m1", "m2"}


def test_empty_event_is_noop(handler):
    assert handler.handler({"Records": []}) == {"batchItemFailures": []}
