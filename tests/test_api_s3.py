"""HTTP-level tests for the new /s3, /s3/object, /demo/wan-* endpoints.

Uses fastapi.testclient.TestClient with create_app() so each test gets an
isolated app + S3Watcher (which we then patch with a mock client).

We don't hit AWS or shell out to ssh in these tests:
  - S3Watcher's _client is replaced with a MagicMock per-test.
  - subprocess.run is monkeypatched at module level when WAN endpoints are hit.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from expanso_security_camera.orchestrator.api import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Ensure the watcher boots in laptop-dev mode (no real bucket).
    monkeypatch.delenv("ARMYX_S3_BUCKET", raising=False)
    monkeypatch.delenv("ARMYX_JETSON_HOST", raising=False)

    a = create_app(
        db_path=str(tmp_path / "orch.db"),
        triggers_path=str(tmp_path / "triggers.yaml"),
        snapshots_dir=str(tmp_path / "snapshots"),
        ndjson_path=str(tmp_path / "events.ndjson"),
        fake_mode=True,
        s3_bucket=None,
        jetson_host="",
    )
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


class TestS3Endpoint:
    def test_s3_when_unconfigured(self, client):
        r = client.get("/s3")
        assert r.status_code == 200
        body = r.json()
        # Dashboard renders this as "not configured" on the egress tile.
        assert body["enabled"] is False
        assert body["object_count"] == 0
        assert body["recent_keys"] == []

    def test_s3_object_returns_503_when_unconfigured(self, client):
        r = client.get("/s3/object", params={"key": "events/x.json"})
        # No S3 client => orchestrator returns 503 with an error body, never
        # 500 (we don't want the dashboard's poll to crash).
        assert r.status_code == 503
        assert "error" in r.json()


class TestS3EndpointWithMockedClient:
    """Patch the watcher's S3 client to verify successful paths without AWS."""

    def _install_mock_client(self, app, recent_keys):
        # Pull the watcher off the closure-captured factory state via the
        # routes' dependencies. Easier: re-reach into the create_app closure
        # through the app's state isn't exposed; instead, monkeypatch the
        # S3Watcher class so the next instance uses our mock.
        # Simpler: the existing fixture already created the watcher, we
        # just walk it through the route handler module attributes.
        # The cleanest hook: every endpoint closes over `s3` from create_app,
        # so we set s3._client and s3._state directly on whatever instance
        # the app has. We grab it via a sentinel route.
        raise NotImplementedError  # see test_s3_full_flow below for the simpler approach

    def test_s3_full_flow_via_create_app_arguments(self, tmp_path, monkeypatch):
        """End-to-end: configure a bucket, swap client, call /s3 + /s3/object."""
        from expanso_security_camera.orchestrator import s3_watcher as sw_mod

        # Spy on S3Watcher.__init__ so we can capture the instance the app uses.
        captured: dict = {}
        real_init = sw_mod.S3Watcher.__init__

        def spy_init(self, *args, **kwargs):
            real_init(self, *args, **kwargs)
            captured["watcher"] = self

        monkeypatch.setattr(sw_mod.S3Watcher, "__init__", spy_init)

        a = create_app(
            db_path=str(tmp_path / "o.db"),
            triggers_path=str(tmp_path / "t.yaml"),
            snapshots_dir=str(tmp_path / "snap"),
            ndjson_path=str(tmp_path / "e.ndjson"),
            fake_mode=True,
            s3_bucket="armyx-tech-edge-events-demo",
            jetson_host="",
        )
        watcher = captured["watcher"]
        # Force-enable + replace the (None or real) client with a mock.
        watcher._state.enabled = True
        watcher._client = MagicMock()
        # Pre-populate state so /s3 returns something interesting.
        watcher._state.object_count = 47
        watcher._state.recent_keys = [
            {
                "key": "events/dt=2026-05-01/sensor-north/1.json",
                "size": 320,
                "last_modified": 1700000000.0,
            }
        ]
        watcher._state.last_poll_ok = True

        client = TestClient(a)

        r = client.get("/s3")
        body = r.json()
        assert body["enabled"] is True
        assert body["bucket"] == "armyx-tech-edge-events-demo"
        assert body["object_count"] == 47
        assert len(body["recent_keys"]) == 1

        # /s3/object happy path
        body_bytes = MagicMock()
        body_bytes.read.return_value = b'{"node":"sensor-north"}'

        class _Dt:
            @staticmethod
            def timestamp():
                return 1700000000.0

        watcher._client.get_object.return_value = {
            "Body": body_bytes,
            "ContentLength": 23,
            "LastModified": _Dt(),
            "ContentType": "application/json",
        }
        r = client.get(
            "/s3/object",
            params={"key": "events/dt=2026-05-01/sensor-north/1.json"},
        )
        assert r.status_code == 200
        out = r.json()
        assert out["body"] == '{"node":"sensor-north"}'
        assert out["truncated"] is False


class TestWanDemoEndpoints:
    def test_wan_down_in_fake_mode(self, client):
        # No JETSON_HOST set => cosmetic-only flip, but the endpoint still
        # returns 200 and the cloud_up flag goes False.
        r = client.post("/demo/wan-down")
        assert r.status_code == 200
        body = r.json()
        assert body["cloud_up"] is False
        # In fake mode the SSH call wasn't made => real_toggle_ok is False
        # (cosmetic_only=True). The dashboard still treats this as "down".
        assert body["real_toggle_ok"] is True  # ok=True, cosmetic_only=True
        assert "not set" in body["detail"]

    def test_wan_up_in_fake_mode(self, client):
        client.post("/demo/wan-down")
        r = client.post("/demo/wan-up")
        assert r.status_code == 200
        assert r.json()["cloud_up"] is True

    def test_wan_down_with_real_ssh_success(self, tmp_path, monkeypatch):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        a = create_app(
            db_path=str(tmp_path / "o.db"),
            triggers_path=str(tmp_path / "t.yaml"),
            snapshots_dir=str(tmp_path / "snap"),
            ndjson_path=str(tmp_path / "e.ndjson"),
            fake_mode=True,
            s3_bucket=None,
            jetson_host="nvidia@jetson",
        )
        client = TestClient(a)
        r = client.post("/demo/wan-down")
        assert r.status_code == 200
        assert r.json()["real_toggle_ok"] is True
