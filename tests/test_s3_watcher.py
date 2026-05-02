"""Tests for orchestrator/s3_watcher.py.

We don't hit AWS in unit tests. Three things we DO want to verify:
  1. Empty / unconfigured state surfaces cleanly (object_count=0, enabled=False)
     so the orchestrator boots even when no bucket is configured.
  2. The state→dict shape stays stable — the dashboard depends on these keys
     and renames here would silently break the UI.
  3. fetch_object's prefix guard rejects keys outside the configured prefix
     (stops a misbehaving caller from probing arbitrary S3 keys via /s3/object).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from expanso_security_camera.orchestrator.s3_watcher import (
    STALL_THRESHOLD_SEC,
    S3State,
    S3Watcher,
)


class TestS3State:
    def test_empty_state_to_dict(self):
        s = S3State()
        d = s.to_dict()
        # Dashboard checks .enabled before painting; this must be False on init.
        assert d["enabled"] is False
        assert d["object_count"] == 0
        assert d["last_upload_ts"] is None
        assert d["seconds_since_upload"] is None
        assert d["stalled"] is False
        assert d["recent_keys"] == []

    def test_stalled_after_threshold(self):
        s = S3State(last_upload_ts=time.time() - (STALL_THRESHOLD_SEC + 5))
        assert s.to_dict()["stalled"] is True

    def test_not_stalled_when_fresh(self):
        s = S3State(last_upload_ts=time.time() - 1)
        assert s.to_dict()["stalled"] is False

    def test_required_keys_for_dashboard(self):
        # Locking these in: ws_client.js renderS3() reads exactly these names.
        keys = set(S3State().to_dict().keys())
        required = {
            "bucket",
            "region",
            "enabled",
            "object_count",
            "last_upload_ts",
            "seconds_since_upload",
            "stalled",
            "last_poll_ts",
            "last_poll_ok",
            "last_error",
            "recent_keys",
        }
        assert required.issubset(keys), f"missing keys: {required - keys}"


class TestS3WatcherUnconfigured:
    def test_no_bucket_disables_watcher(self, monkeypatch):
        monkeypatch.delenv("ARMYX_S3_BUCKET", raising=False)
        w = S3Watcher(bucket=None)
        snap = w.snapshot()
        assert snap["enabled"] is False
        assert snap["bucket"] == ""

    def test_start_is_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv("ARMYX_S3_BUCKET", raising=False)
        w = S3Watcher(bucket=None)
        w.start()
        # Thread shouldn't have been created.
        assert w._thread is None


class TestS3WatcherWithMockedClient:
    @pytest.fixture
    def watcher(self):
        """A Watcher with its boto3 client replaced by a MagicMock.

        We bypass _build_client by constructing the watcher with no bucket,
        then setting attributes directly. This avoids importing boto3 in tests.
        """
        w = S3Watcher(bucket=None)
        w._state.bucket = "test-bucket"
        w._state.enabled = True
        w._client = MagicMock()
        return w

    def test_fetch_object_rejects_keys_outside_prefix(self, watcher):
        # Prefix guard: only events/ keys are servable. A caller sending
        # a different prefix gets None back, never an S3 round-trip.
        result = watcher.fetch_object("not-events/secret.txt")
        assert result is None
        watcher._client.get_object.assert_not_called()

    def test_fetch_object_calls_s3_for_valid_prefix(self, watcher):
        body = MagicMock()
        body.read.return_value = b'{"hello":"world"}'
        watcher._client.get_object.return_value = {
            "Body": body,
            "ContentLength": 18,
            "LastModified": _UtcNow(),
            "ContentType": "application/json",
        }
        result = watcher.fetch_object("events/dt=2026-05-01/sensor-north/x.json")
        assert result is not None
        assert result["key"] == "events/dt=2026-05-01/sensor-north/x.json"
        assert result["body"] == '{"hello":"world"}'
        assert result["truncated"] is False

    def test_fetch_object_marks_truncated_when_oversize(self, watcher):
        # max_bytes=10 means 11+ bytes returned should mark truncated=True.
        body = MagicMock()
        body.read.return_value = b"0123456789ABCDEF"  # 16 bytes
        watcher._client.get_object.return_value = {
            "Body": body,
            "ContentLength": 16,
            "LastModified": _UtcNow(),
            "ContentType": "application/json",
        }
        result = watcher.fetch_object("events/dt=2026-05-01/sensor-north/x.json", max_bytes=10)
        assert result["truncated"] is True
        assert len(result["body"]) == 10

    def test_fetch_object_returns_error_dict_on_s3_failure(self, watcher):
        watcher._client.get_object.side_effect = RuntimeError("denied")
        result = watcher.fetch_object("events/dt=2026-05-01/x.json")
        assert "error" in result
        assert "RuntimeError" in result["error"]

    def test_poll_records_count_and_recent_keys(self, watcher):
        # Two keys returned across two pages, sorted by last_modified desc in result.
        page = {
            "Contents": [
                {"Key": "events/a.json", "Size": 100, "LastModified": _UtcAt(1000)},
                {"Key": "events/b.json", "Size": 200, "LastModified": _UtcAt(2000)},
            ]
        }
        watcher._client.get_paginator.return_value.paginate.return_value = [page]
        watcher._poll_once()
        snap = watcher.snapshot()
        assert snap["object_count"] == 2
        assert snap["last_poll_ok"] is True
        assert snap["recent_keys"][0]["key"] == "events/b.json"  # newer first
        assert snap["last_upload_ts"] == 2000

    def test_poll_records_error_on_exception(self, watcher):
        watcher._client.get_paginator.side_effect = RuntimeError("kaboom")
        watcher._poll_once()
        snap = watcher.snapshot()
        assert snap["last_poll_ok"] is False
        assert "RuntimeError" in snap["last_error"]


class _UtcNow:
    """Stand-in for boto3's datetime objects (we only need .timestamp())."""

    def timestamp(self) -> float:
        return time.time()


class _UtcAt:
    def __init__(self, ts: float) -> None:
        self._ts = ts

    def timestamp(self) -> float:
        return self._ts
