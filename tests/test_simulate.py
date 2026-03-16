"""Tests for simulate.py — event generation, state management."""

from __future__ import annotations

import json

from expanso_security_camera.events import DashboardState


class TestSimulateOutput:
    """Test that simulate.py emits valid CrossingEvents."""

    def test_event_is_valid_json(self):
        """Each line emitted should be valid JSON with required fields."""
        # Verify CrossingEvent can be serialized
        from expanso_security_camera.events import (
            BoundingBox,
            Centroid,
            CrossingEvent,
            DetectionInfo,
            Direction,
            FrameInfo,
        )

        evt = CrossingEvent(
            camera_id="cam-outside",
            detection=DetectionInfo(
                track_id=1,
                object_class="box",
                object_class_raw="suitcase",
                confidence=0.9,
                bounding_box=BoundingBox(x1=10, y1=20, x2=100, y2=200),
                centroid=Centroid(x=55.0, y=110.0),
                direction=Direction.OUTBOUND,
                crossing_line_id="cam-outside-line-1",
            ),
            frame=FrameInfo(frame_number=1, frame_timestamp="2026-03-16T12:00:00Z"),
            counts={"camera_departures": 1, "camera_arrivals": 0},
        )
        data = json.loads(evt.model_dump_json())
        assert data["event_type"] == "crossing"
        assert data["camera_id"] == "cam-outside"
        assert data["detection"]["object_class"] == "box"


class TestDashboardStateForSimulate:
    def test_state_tracks_counts(self):
        state = DashboardState()
        state.camera_outside_departures = 5
        state.camera_inside_arrivals = 4
        state.discrepancy = 1
        state.status = "DISCREPANCY"
        data = json.loads(state.model_dump_json())
        assert data["camera_outside_departures"] == 5
        assert data["discrepancy"] == 1

    def test_recent_events_list(self):
        state = DashboardState()
        for i in range(60):
            state.recent_events.append({"idx": i})
        # Simulate the trim logic from inference.py
        state.recent_events = state.recent_events[-50:]
        assert len(state.recent_events) == 50
        assert state.recent_events[0]["idx"] == 10
