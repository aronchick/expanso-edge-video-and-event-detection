"""Tests for server.py — FastAPI endpoints, file-based serving."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from expanso_security_camera.server import app


@pytest.fixture
def client(tmp_path):
    """Test client with patched paths pointing to tmp_path."""
    with (
        patch.object(
            __import__("expanso_security_camera.server", fromlist=["STATE_PATH"]),
            "STATE_PATH",
            tmp_path / "state.json",
        ),
        patch.object(
            __import__("expanso_security_camera.server", fromlist=["SNAPSHOTS_DIR"]),
            "SNAPSHOTS_DIR",
            tmp_path / "snapshots",
        ),
    ):
        (tmp_path / "snapshots").mkdir()
        yield TestClient(app)


class TestStateEndpoint:
    def test_returns_defaults_when_no_file(self, client):
        resp = client.get("/api/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "WAITING"
        assert data["discrepancy"] == 0

    def test_returns_state_from_file(self, client, tmp_path):
        state = {
            "session_id": "test",
            "camera_outside_departures": 3,
            "camera_inside_arrivals": 2,
            "discrepancy": 1,
            "status": "DISCREPANCY",
            "last_event_ts": "",
            "first_discrepancy_at": None,
            "recent_events": [],
            "inference_fps": 0.0,
            "pipeline_status": "running",
            "detect_mode": "box",
        }
        (tmp_path / "state.json").write_text(json.dumps(state))
        resp = client.get("/api/state")
        assert resp.status_code == 200
        assert resp.json()["discrepancy"] == 1

    def test_handles_corrupt_json(self, client, tmp_path):
        (tmp_path / "state.json").write_text("{bad json")
        resp = client.get("/api/state")
        assert resp.status_code == 200
        assert resp.json()["status"] == "WAITING"


class TestSnapshotEndpoint:
    def test_returns_jpeg(self, client, tmp_path):
        # Write a minimal JPEG (just the header bytes)
        import cv2
        import numpy as np

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        _, buf = cv2.imencode(".jpg", frame)
        (tmp_path / "snapshots" / "cam-test.jpg").write_bytes(buf.tobytes())

        resp = client.get("/api/snapshot/cam-test")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.headers["cache-control"] == "no-cache, no-store"

    def test_404_unknown_camera(self, client):
        resp = client.get("/api/snapshot/nonexistent")
        assert resp.status_code == 404

    def test_annotate_param_ignored(self, client, tmp_path):
        """annotate param is accepted but snapshots are pre-annotated on disk."""
        import cv2
        import numpy as np

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        _, buf = cv2.imencode(".jpg", frame)
        (tmp_path / "snapshots" / "cam-test.jpg").write_bytes(buf.tobytes())

        resp = client.get("/api/snapshot/cam-test?annotate=true")
        assert resp.status_code == 200


class TestDetectionsEndpoint:
    def test_returns_detections(self, client, tmp_path):
        data = {
            "cam-outside": {"boxes": 5, "status": "online"},
            "cam-inside": {"boxes": 3, "status": "online"},
        }
        (tmp_path / "snapshots" / "detections.json").write_text(json.dumps(data))
        resp = client.get("/api/detections")
        assert resp.status_code == 200
        assert resp.json()["cam-outside"]["boxes"] == 5

    def test_503_when_no_file(self, client):
        resp = client.get("/api/detections")
        assert resp.status_code == 503

    def test_handles_corrupt_json(self, client, tmp_path):
        (tmp_path / "snapshots" / "detections.json").write_text("not json")
        resp = client.get("/api/detections")
        assert resp.status_code == 503


class TestResetEndpoint:
    def test_writes_command(self, client, tmp_path):
        with patch(
            "expanso_security_camera.server.Path",
            side_effect=lambda x: tmp_path / "commands.json" if x == "commands.json" else Path(x),
        ):
            resp = client.post("/api/reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestHTMLEndpoints:
    def test_index(self, client):
        resp = client.get("/")
        # May return 200 (if public/ exists) or 404
        assert resp.status_code in (200, 404)

    def test_architecture(self, client):
        resp = client.get("/architecture")
        assert resp.status_code in (200, 404)
