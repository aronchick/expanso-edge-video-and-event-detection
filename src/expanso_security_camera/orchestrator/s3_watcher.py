"""Polls the armyx-tech S3 archive bucket on the Mac side and exposes
state to the dashboard.

This is the visual proof for the demo's Beat D: judges see object count
climb in lockstep with events while the cluster is online; plateau when
it's offline; surge on reconnect. The orchestrator runs on the Mac, the
Mac retains its own internet path to S3 (independent of the Jetson's
toggleable WAN), so this watcher keeps reporting the truth even while
the Jetson is "off the world."

Auth: reads ~/.aws/credentials profile [armyx-tech] (set up by
scripts/bootstrap_armyx_tech.sh). Falls back to default chain.

Failure mode: if boto3 isn't installed or no bucket is configured, the
watcher silently no-ops and surfaces empty state — the rest of the
orchestrator still runs.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

POLL_INTERVAL_SEC = 5.0
RECENT_KEY_LIMIT = 50
LIST_PAGE_SIZE = 1000
STALL_THRESHOLD_SEC = 15.0


@dataclass
class S3State:
    bucket: str = ""
    region: str = ""
    enabled: bool = False
    object_count: int = 0
    last_upload_ts: float | None = None
    last_poll_ts: float = 0.0
    last_poll_ok: bool = False
    last_error: str | None = None
    recent_keys: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        now = time.time()
        seconds_since_upload = (
            now - self.last_upload_ts if self.last_upload_ts is not None else None
        )
        return {
            "bucket": self.bucket,
            "region": self.region,
            "enabled": self.enabled,
            "object_count": self.object_count,
            "last_upload_ts": self.last_upload_ts,
            "seconds_since_upload": seconds_since_upload,
            "stalled": (
                seconds_since_upload is not None and seconds_since_upload > STALL_THRESHOLD_SEC
            ),
            "last_poll_ts": self.last_poll_ts,
            "last_poll_ok": self.last_poll_ok,
            "last_error": self.last_error,
            "recent_keys": self.recent_keys,
        }


class S3Watcher:
    """Background thread that polls S3 and caches state for the dashboard.

    Thread-safe: snapshot() copies the dataclass under a lock.
    """

    def __init__(
        self,
        bucket: str | None = None,
        region: str | None = None,
        profile: str | None = None,
        prefix: str = "events/",
    ) -> None:
        self._lock = threading.Lock()
        self._stop = False
        self._state = S3State(
            bucket=bucket or os.environ.get("ARMYX_S3_BUCKET", ""),
            region=region or os.environ.get("ARMYX_AWS_REGION", "us-west-2"),
        )
        self._profile = profile or os.environ.get("ARMYX_AWS_PROFILE", "armyx-tech")
        self._prefix = prefix
        self._client = self._build_client()
        self._state.enabled = self._client is not None and bool(self._state.bucket)
        self._thread: threading.Thread | None = None

    def _build_client(self):
        bucket = self._state.bucket
        if not bucket:
            return None
        try:
            import boto3  # lazy: keeps boto3 optional at import time
        except ImportError:
            self._state.last_error = "boto3 not installed; pip/uv add boto3"
            return None
        try:
            session = boto3.Session(profile_name=self._profile, region_name=self._state.region)
            return session.client("s3")
        except Exception as exc:
            try:
                # Profile may not exist on this Mac; fall back to default chain.
                import boto3

                return boto3.Session(region_name=self._state.region).client("s3")
            except Exception as fallback_exc:
                self._state.last_error = f"boto3 client init failed: {exc} / {fallback_exc}"
                return None

    def start(self) -> None:
        if self._thread is not None:
            return
        if not self._state.enabled:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="s3-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    def _loop(self) -> None:
        while not self._stop:
            self._poll_once()
            time.sleep(POLL_INTERVAL_SEC)

    def _poll_once(self) -> None:
        client = self._client
        if client is None:
            return
        bucket = self._state.bucket
        try:
            # Count objects via paginator (cheap; the bucket only has demo events).
            paginator = client.get_paginator("list_objects_v2")
            total = 0
            recent: list[dict[str, Any]] = []
            for page in paginator.paginate(
                Bucket=bucket, Prefix=self._prefix, PaginationConfig={"PageSize": LIST_PAGE_SIZE}
            ):
                contents = page.get("Contents", [])
                total += len(contents)
                for obj in contents:
                    recent.append(
                        {
                            "key": obj["Key"],
                            "size": obj["Size"],
                            "last_modified": obj["LastModified"].timestamp(),
                        }
                    )
            recent.sort(key=lambda r: r["last_modified"], reverse=True)
            recent = recent[:RECENT_KEY_LIMIT]
            last_upload = recent[0]["last_modified"] if recent else None
        except Exception as exc:
            with self._lock:
                self._state.last_poll_ok = False
                self._state.last_poll_ts = time.time()
                self._state.last_error = f"{type(exc).__name__}: {exc}"
            return

        with self._lock:
            self._state.object_count = total
            self._state.recent_keys = recent
            self._state.last_upload_ts = last_upload
            self._state.last_poll_ts = time.time()
            self._state.last_poll_ok = True
            self._state.last_error = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._state.to_dict()

    def fetch_object(self, key: str, max_bytes: int = 64 * 1024) -> dict[str, Any] | None:
        """Fetch an object's body for the dashboard's per-event viewer.

        Caps the response so a misbehaving caller can't DoS the orchestrator.
        Demo events are ~1KB, so 64KB is generous.
        """
        client = self._client
        if client is None or not self._state.bucket:
            return None
        if not key.startswith(self._prefix):
            return None  # only serve from the configured prefix
        try:
            obj = client.get_object(Bucket=self._state.bucket, Key=key)
            body = obj["Body"].read(max_bytes + 1)
            truncated = len(body) > max_bytes
            return {
                "key": key,
                "size": obj["ContentLength"],
                "last_modified": obj["LastModified"].timestamp(),
                "content_type": obj.get("ContentType", "application/octet-stream"),
                "body": body[:max_bytes].decode("utf-8", errors="replace"),
                "truncated": truncated,
            }
        except Exception as exc:
            return {"key": key, "error": f"{type(exc).__name__}: {exc}"}
