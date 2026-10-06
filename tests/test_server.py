"""Tests for server.py — FastAPI endpoints, file-based serving."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from expanso_security_camera.server import PUBLIC_DIR, app


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
        assert resp.status_code == 200
        assert "Box Transfer Monitor" in resp.text

    def test_architecture(self, client):
        resp = client.get("/architecture")
        assert resp.status_code == 200
        assert "Architecture" in resp.text


PAGES = ("index.html", "architecture.html", "live-viewer.html", "record-viewer.html")
SHARED_ASSETS = (
    "box.css",
    "theme.js",
    "dashboard.js",
    "live-viewer.js",
    "fonts/fonts.css",
)


def _public_text(name: str) -> str:
    return (PUBLIC_DIR / name).read_text(encoding="utf-8")


class TestBoxPages:
    """The four box-counting pages: served, themed, and free of banned copy."""

    @pytest.mark.parametrize("name", PAGES + SHARED_ASSETS)
    def test_served_from_static(self, client, name):
        assert client.get(f"/static/{name}").status_code == 200

    @pytest.mark.parametrize("name", PAGES)
    def test_light_default_with_explicit_dark_toggle(self, name):
        text = _public_text(name)
        assert 'data-theme="light"' in text
        assert 'class="btn theme-toggle"' in text
        assert 'aria-pressed="false"' in text

    def test_dark_theme_ignores_os_preference(self):
        css = _public_text("box.css")
        assert 'html[data-theme="dark"]' in css or ':root[data-theme="dark"]' in css
        assert "prefers-color-scheme" not in css
        assert "prefers-color-scheme" not in _public_text("theme.js")

    def test_theme_choice_is_stored_defensively(self):
        js = _public_text("theme.js")
        assert "localStorage" in js
        assert js.count("try {") >= 2

    @pytest.mark.parametrize("name", PAGES)
    def test_every_page_links_to_the_others(self, name):
        text = _public_text(name)
        for target in ("architecture", "live-viewer", "record-viewer"):
            if target not in name:
                assert target in text

    @pytest.mark.parametrize("name", PAGES + ("box.css", "dashboard.js", "live-viewer.js"))
    def test_no_banned_visible_copy_or_decoration(self, name):
        text = _public_text(name)
        lowered = text.lower()
        for word in ("illustrative", "modeled", "simulated", "demo scale"):
            assert word not in lowered
        assert "\u2014" not in text  # em dash
        assert "&mdash;" not in lowered
        assert "gradient" not in lowered
        assert "box-shadow" not in lowered
        assert "border-left" not in lowered
        assert "border-right" not in lowered
        assert "cdn." not in lowered
        assert not [c for c in text if "\U0001f300" <= c <= "\U0001faff"]
        assert not [c for c in text if "\u2600" <= c <= "\u27bf"]

    @pytest.mark.parametrize("name", PAGES[:3])
    def test_local_assets_exist(self, name):
        text = _public_text(name)
        refs = re.findall(r'(?:href|src)="([^"#]+)"', text)
        local = [r for r in refs if not r.startswith(("http", "/api", "/architecture", "/guide"))]
        for ref in local:
            if ref in {"/"} or ref.endswith(".html"):
                continue
            rel = ref.removeprefix("/static/")
            assert (PUBLIC_DIR / rel).is_file(), ref

    def test_dashboard_shows_reconciliation_and_inventory(self):
        """The arrivals/departures reconciliation from state.json must stay on the page."""
        html = _public_text("index.html")
        js = _public_text("dashboard.js")
        for dom_id in (
            "departures",
            "arrivals",
            "recon-card",
            "recon-status",
            "inventory-banner",
            "baseline-btn",
            "reset-btn",
            "manual-override",
        ):
            assert f'id="{dom_id}"' in html
        for field in (
            "camera_outside_departures",
            "camera_inside_arrivals",
            "discrepancy",
            "first_discrepancy_at",
        ):
            assert field in js
        assert "unaccounted for" in js

    def test_dashboard_reports_unreachable_sources(self):
        html = _public_text("index.html")
        js = _public_text("dashboard.js")
        assert 'id="api-notice"' in html
        assert "unreachable" in js
        assert "No snapshot from" in js
        assert "go2rtc" in _public_text("live-viewer.js")

    def test_raw_json_is_pretty_printed(self):
        assert "JSON.stringify(app.state, null, 2)" in _public_text("dashboard.js")
        assert "pre-wrap" in _public_text("box.css")


class TestGuideServedByBoxServer:
    def test_guide_and_fonts_are_mounted(self, client):
        assert client.get("/guide/").status_code == 200
        assert client.get("/fonts/fonts.css").status_code == 200
