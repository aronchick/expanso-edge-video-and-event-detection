"""Tests for events.py — schemas, emit, state writing."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from expanso_security_camera.events import (
    BoundingBox,
    Centroid,
    CrossingEvent,
    DashboardState,
    DetectionInfo,
    Direction,
    DiscrepancyAlert,
    FrameInfo,
    ReconciliationEvent,
    WindowedCountEvent,
    emit_event,
    write_state,
)


class TestDirection:
    def test_values(self):
        assert Direction.INBOUND == "inbound"
        assert Direction.OUTBOUND == "outbound"

    def test_string_comparison(self):
        assert Direction.INBOUND.value == "inbound"


class TestBoundingBox:
    def test_creation(self):
        bb = BoundingBox(x1=10, y1=20, x2=100, y2=200)
        assert bb.x1 == 10
        assert bb.x2 == 100

    def test_serialization(self):
        bb = BoundingBox(x1=10, y1=20, x2=100, y2=200)
        data = bb.model_dump()
        assert data == {"x1": 10, "y1": 20, "x2": 100, "y2": 200}


class TestCrossingEvent:
    def _make_event(self, **kwargs):
        defaults = {
            "camera_id": "cam-test",
            "detection": DetectionInfo(
                track_id=1,
                object_class="box",
                object_class_raw="suitcase",
                confidence=0.85,
                bounding_box=BoundingBox(x1=10, y1=20, x2=100, y2=200),
                centroid=Centroid(x=55.0, y=110.0),
                direction=Direction.OUTBOUND,
                crossing_line_id="cam-test-line-1",
            ),
            "frame": FrameInfo(
                frame_number=42,
                frame_timestamp="2026-03-16T12:00:00Z",
            ),
        }
        defaults.update(kwargs)
        return CrossingEvent(**defaults)

    def test_defaults(self):
        evt = self._make_event()
        assert evt.schema_version == "1.0.0"
        assert evt.event_type == "crossing"
        assert evt.event_id.startswith("evt_")
        assert len(evt.event_id) == 16  # "evt_" + 12 hex chars

    def test_unique_ids(self):
        e1 = self._make_event()
        e2 = self._make_event()
        assert e1.event_id != e2.event_id

    def test_timestamp_auto(self):
        evt = self._make_event()
        assert "T" in evt.timestamp  # ISO format

    def test_json_roundtrip(self):
        evt = self._make_event()
        data = json.loads(evt.model_dump_json())
        assert data["camera_id"] == "cam-test"
        assert data["detection"]["object_class"] == "box"
        assert data["detection"]["confidence"] == 0.85

    def test_counts_default_empty(self):
        evt = self._make_event()
        assert evt.counts == {}


class TestReconciliationEvent:
    def test_creation(self):
        evt = ReconciliationEvent(
            camera_outside_id="cam-outside",
            camera_inside_id="cam-inside",
            outside_departures=5,
            inside_arrivals=4,
            discrepancy=1,
            status="DISCREPANCY",
        )
        assert evt.event_id.startswith("rec_")
        assert evt.discrepancy == 1

    def test_match_status(self):
        evt = ReconciliationEvent(
            camera_outside_id="a",
            camera_inside_id="b",
            outside_departures=5,
            inside_arrivals=5,
            discrepancy=0,
            status="MATCH",
        )
        assert evt.status == "MATCH"


class TestDiscrepancyAlert:
    def test_creation(self):
        alert = DiscrepancyAlert(
            message="1 box missing",
            outside_count=5,
            inside_count=4,
            missing_count=1,
            first_detected="2026-03-16T12:00:00Z",
        )
        assert alert.event_id.startswith("alert_")
        assert alert.severity == "warning"


class TestWindowedCountEvent:
    def test_creation(self):
        evt = WindowedCountEvent(
            camera_id="cam-test",
            window_start="2026-03-16T12:00:00Z",
            window_end="2026-03-16T12:00:30Z",
            departures=3,
        )
        assert evt.event_id.startswith("wnd_")
        assert evt.departures == 3
        assert evt.window_duration_seconds == 30


class TestDashboardState:
    def test_defaults(self):
        state = DashboardState()
        assert state.session_id.startswith("sess_")
        assert state.status == "MATCH"
        assert state.discrepancy == 0
        assert state.detect_mode == "box"
        assert state.recent_events == []

    def test_serialization(self):
        state = DashboardState(
            camera_outside_departures=3,
            camera_inside_arrivals=2,
            discrepancy=1,
            status="DISCREPANCY",
        )
        data = json.loads(state.model_dump_json())
        assert data["camera_outside_departures"] == 3
        assert data["discrepancy"] == 1


class TestEmitEvent:
    def test_writes_json_line(self):
        evt = DashboardState()
        buf = StringIO()
        with patch("sys.stdout", buf):
            emit_event(evt)
        output = buf.getvalue()
        assert output.endswith("\n")
        data = json.loads(output.strip())
        assert "session_id" in data

    def test_multiple_events(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            emit_event(DashboardState())
            emit_event(DashboardState())
        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 2


class TestWriteState:
    def test_atomic_write(self, tmp_path):
        state = DashboardState(
            camera_outside_departures=5,
            status="MATCH",
        )
        filepath = str(tmp_path / "state.json")
        write_state(filepath, state)
        data = json.loads((tmp_path / "state.json").read_text())
        assert data["camera_outside_departures"] == 5
        # Temp file should be cleaned up
        assert not (tmp_path / "state.json.tmp").exists()

    def test_overwrite(self, tmp_path):
        filepath = str(tmp_path / "state.json")
        write_state(filepath, DashboardState(status="MATCH"))
        write_state(filepath, DashboardState(status="DISCREPANCY"))
        data = json.loads((tmp_path / "state.json").read_text())
        assert data["status"] == "DISCREPANCY"
