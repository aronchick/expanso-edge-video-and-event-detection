"""Tests for counter.py — line crossing, filtering, state machine."""

from __future__ import annotations

from unittest.mock import patch

from expanso_security_camera.counter import (
    CounterState,
    CounterStateMachine,
    CountingLine,
    DirectionFilter,
    SizeFilter,
)


class TestCountingLine:
    def test_crossing_down(self):
        line = CountingLine(y=200, count_direction="down")
        assert line.is_crossing(190, 210) is True
        assert line.is_crossing(210, 190) is False

    def test_crossing_up(self):
        line = CountingLine(y=200, count_direction="up")
        assert line.is_crossing(210, 190) is True
        assert line.is_crossing(190, 210) is False

    def test_no_crossing_same_side(self):
        line = CountingLine(y=200, count_direction="down")
        assert line.is_crossing(100, 150) is False
        assert line.is_crossing(210, 220) is False

    def test_exact_line_crossing_down(self):
        line = CountingLine(y=200, count_direction="down")
        # prev < y <= curr
        assert line.is_crossing(199, 200) is True
        assert line.is_crossing(200, 201) is False  # prev == y, not <

    def test_exact_line_crossing_up(self):
        line = CountingLine(y=200, count_direction="up")
        assert line.is_crossing(201, 200) is True
        assert line.is_crossing(200, 199) is False

    def test_x_range_inclusive(self):
        line = CountingLine(y=200, x_start=100, x_end=500)
        assert line.is_in_x_range(100) is True
        assert line.is_in_x_range(500) is True
        assert line.is_in_x_range(300) is True

    def test_x_range_exclusive(self):
        line = CountingLine(y=200, x_start=100, x_end=500)
        assert line.is_in_x_range(99) is False
        assert line.is_in_x_range(501) is False


class TestSizeFilter:
    def test_passes_normal_box(self):
        sf = SizeFilter(min_area=3000, max_area=200000, min_width=40)
        assert sf.passes(0, 0, 100, 50) is True  # area=5000, width=100

    def test_rejects_too_small(self):
        sf = SizeFilter(min_area=3000, max_area=200000, min_width=40)
        assert sf.passes(0, 0, 20, 20) is False  # area=400

    def test_rejects_too_large(self):
        sf = SizeFilter(min_area=3000, max_area=200000, min_width=40)
        assert sf.passes(0, 0, 500, 500) is False  # area=250000

    def test_rejects_too_narrow(self):
        sf = SizeFilter(min_area=3000, max_area=200000, min_width=40)
        assert sf.passes(0, 0, 30, 200) is False  # width=30 < 40

    def test_boundary_values(self):
        sf = SizeFilter(min_area=100, max_area=1000, min_width=10)
        assert sf.passes(0, 0, 10, 10) is True  # area=100, exactly min
        assert sf.passes(0, 0, 100, 10) is True  # area=1000, exactly max


class TestDirectionFilter:
    def test_right_movement(self):
        df = DirectionFilter(allowed_direction="right", min_displacement=5.0)
        assert df.passes(100, 110) is True

    def test_left_movement(self):
        df = DirectionFilter(allowed_direction="left", min_displacement=5.0)
        assert df.passes(110, 100) is True

    def test_wrong_direction_rejected(self):
        df = DirectionFilter(allowed_direction="right", min_displacement=5.0)
        assert df.passes(110, 100) is False

    def test_ambiguous_passes(self):
        """Small displacement should pass (let size filter catch it)."""
        df = DirectionFilter(allowed_direction="right", min_displacement=5.0)
        assert df.passes(100, 102) is True  # dx=2 < 5

    def test_zero_displacement_passes(self):
        df = DirectionFilter(allowed_direction="right", min_displacement=5.0)
        assert df.passes(100, 100) is True


class TestCounterStateMachine:
    def _make_counter(self, **kwargs):
        defaults = {
            "camera_id": "test-cam",
            "counting_line": CountingLine(y=200, x_start=0, x_end=640),
            "direction_filter": DirectionFilter(allowed_direction="right"),
            "size_filter": SizeFilter(min_area=100, max_area=200000, min_width=10),
            "cooldown_seconds": 1.0,
        }
        defaults.update(kwargs)
        return CounterStateMachine(**defaults)

    def _make_det(self, track_id, cx, cy, w=80, h=100, conf=0.8):
        x1 = int(cx - w / 2)
        y1 = int(cy - h / 2)
        return {
            "track_id": track_id,
            "class_name": "suitcase",
            "class_id": 28,
            "confidence": conf,
            "bbox": (x1, y1, x1 + w, y1 + h),
            "centroid": (cx, cy),
        }

    def test_initial_state(self):
        counter = self._make_counter()
        assert counter.state == CounterState.IDLE
        assert counter.departures == 0
        assert counter.arrivals == 0

    def test_crossing_increments_departures(self):
        counter = self._make_counter()
        # Frame 1: object above line
        det1 = self._make_det(1, 300, 180)
        counter.update([det1], 1)
        # Frame 2: object crosses below line
        det2 = self._make_det(1, 310, 220)
        events = counter.update([det2], 2)
        assert counter.departures == 1
        assert len(events) == 1
        assert events[0]["camera_id"] == "test-cam"

    def test_cooldown_prevents_double_count(self):
        counter = self._make_counter(cooldown_seconds=2.0)
        # Cross line
        counter.update([self._make_det(1, 300, 190)], 1)
        events1 = counter.update([self._make_det(1, 310, 210)], 2)
        assert len(events1) == 1
        # Immediately cross again (same track) — should be in cooldown
        counter.update([self._make_det(1, 320, 190)], 3)
        events2 = counter.update([self._make_det(1, 330, 210)], 4)
        assert len(events2) == 0
        assert counter.departures == 1

    @patch("expanso_security_camera.counter.time.time")
    def test_cooldown_expires(self, mock_time):
        mock_time.return_value = 100.0
        counter = self._make_counter(cooldown_seconds=1.0)
        # Cross line
        counter.update([self._make_det(1, 300, 190)], 1)
        counter.update([self._make_det(1, 310, 210)], 2)
        assert counter.departures == 1
        # Advance time past cooldown
        mock_time.return_value = 102.0
        counter.update([self._make_det(2, 300, 190)], 3)
        counter.update([self._make_det(2, 310, 210)], 4)
        assert counter.departures == 2

    def test_size_filter_rejects(self):
        counter = self._make_counter(
            size_filter=SizeFilter(min_area=50000, max_area=200000, min_width=100)
        )
        # Small detection
        det1 = self._make_det(1, 300, 190, w=20, h=20)
        counter.update([det1], 1)
        det2 = self._make_det(1, 310, 210, w=20, h=20)
        events = counter.update([det2], 2)
        assert len(events) == 0
        assert counter.departures == 0

    def test_reset(self):
        counter = self._make_counter()
        counter.update([self._make_det(1, 300, 190)], 1)
        counter.update([self._make_det(1, 310, 210)], 2)
        assert counter.departures == 1
        counter.reset()
        assert counter.departures == 0
        assert counter.arrivals == 0
        assert counter.state == CounterState.IDLE

    def test_no_detections(self):
        counter = self._make_counter()
        events = counter.update([], 1)
        assert events == []

    def test_centroid_cleanup(self):
        counter = self._make_counter()
        # Add many tracks
        for i in range(120):
            counter.update([self._make_det(i, 300, 100)], i)
        # Should have cleaned up to ~50
        assert len(counter._prev_centroids) <= 100

    def test_crossing_event_structure(self):
        counter = self._make_counter()
        counter.update([self._make_det(1, 300, 190)], 1)
        events = counter.update([self._make_det(1, 310, 210)], 2)
        assert len(events) == 1
        evt = events[0]
        assert "camera_id" in evt
        assert "track_id" in evt
        assert "object_class_raw" in evt
        assert "confidence" in evt
        assert "bbox" in evt
        assert "centroid" in evt
        assert "departures" in evt
