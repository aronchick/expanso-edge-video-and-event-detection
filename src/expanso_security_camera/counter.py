"""State machine line-crossing counter with directional + size filtering.

Each camera gets its own CounterStateMachine instance. The counter
is intentionally independent of ByteTrack's track ID stability —
it uses a state machine with direction and size filtering to count
objects crossing a line.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto


class CounterState(Enum):
    IDLE = auto()
    OBJECT_ENTERING = auto()
    OBJECT_IN_ZONE = auto()
    OBJECT_CROSSING = auto()
    COUNTED = auto()
    COOLDOWN = auto()


@dataclass
class CountingLine:
    """A horizontal counting line defined by y-coordinate and x-range."""

    y: int  # y-coordinate of the line in frame
    x_start: int = 0
    x_end: int = 1280
    count_direction: str = "down"  # "down" = count when crossing downward

    def is_crossing(self, prev_y: float, curr_y: float) -> bool:
        """Check if an object crossed the line between prev and curr y."""
        if self.count_direction == "down":
            return prev_y < self.y <= curr_y
        else:
            return prev_y > self.y >= curr_y

    def is_in_x_range(self, x: float) -> bool:
        return self.x_start <= x <= self.x_end


@dataclass
class SizeFilter:
    """Filter detections by bounding box area to distinguish
    'person carrying box' from 'empty-handed person'."""

    min_area: int = 3000  # minimum bbox area (width * height)
    max_area: int = 200000  # maximum bbox area
    min_width: int = 40  # minimum bbox width

    def passes(self, x1: int, y1: int, x2: int, y2: int) -> bool:
        w = x2 - x1
        h = y2 - y1
        area = w * h
        return self.min_area <= area <= self.max_area and w >= self.min_width


@dataclass
class DirectionFilter:
    """Only count objects moving in one direction along x-axis."""

    allowed_direction: str = "right"  # "right" or "left"
    min_displacement: float = 5.0  # minimum x displacement to determine direction

    def passes(self, prev_x: float, curr_x: float) -> bool:
        dx = curr_x - prev_x
        if abs(dx) < self.min_displacement:
            return True  # ambiguous — let through (size filter catches it)
        if self.allowed_direction == "right":
            return dx > 0
        return dx < 0


@dataclass
class CounterStateMachine:
    """Per-camera state machine counter."""

    camera_id: str
    counting_line: CountingLine
    direction_filter: DirectionFilter
    size_filter: SizeFilter = field(default_factory=SizeFilter)
    cooldown_seconds: float = 3.0
    debounce_frames: int = 3

    # internal state
    state: CounterState = field(default=CounterState.IDLE, init=False)
    departures: int = field(default=0, init=False)
    arrivals: int = field(default=0, init=False)
    _entering_frames: int = field(default=0, init=False)
    _last_count_time: float = field(default=0.0, init=False)
    _prev_centroids: dict[int, tuple[float, float]] = field(default_factory=dict, init=False)
    _crossing_events: list[dict] = field(default_factory=list, init=False)

    def update(
        self,
        detections: list[dict],
        frame_number: int,
    ) -> list[dict]:
        """Process detections for this frame. Returns list of crossing events.

        Each detection dict should have:
            track_id: int
            class_name: str (raw COCO class)
            confidence: float
            bbox: (x1, y1, x2, y2)
            centroid: (cx, cy)
        """
        crossing_events = []
        now = time.time()

        # During cooldown, just update centroids and skip counting
        if self.state == CounterState.COOLDOWN:
            if now - self._last_count_time >= self.cooldown_seconds:
                self.state = CounterState.IDLE
                self._entering_frames = 0
            else:
                self._update_centroids(detections)
                return crossing_events

        # Process each detection
        best_candidate = None
        best_confidence = 0.0

        for det in detections:
            track_id = det["track_id"]
            x1, y1, x2, y2 = det["bbox"]
            cx, cy = det["centroid"]

            # Size filter
            if not self.size_filter.passes(x1, y1, x2, y2):
                continue

            # Direction filter (needs previous position)
            if track_id in self._prev_centroids:
                prev_cx, prev_cy = self._prev_centroids[track_id]
                if not self.direction_filter.passes(prev_cx, cx):
                    continue

                # Check for line crossing
                if self.counting_line.is_crossing(prev_cy, cy):
                    if self.counting_line.is_in_x_range(cx):
                        if det["confidence"] > best_confidence:
                            best_candidate = det
                            best_confidence = det["confidence"]

            # Also track as entering if near the line
            if det["confidence"] > best_confidence and best_candidate is None:
                if abs(cy - self.counting_line.y) < 60:
                    best_candidate = det

        # State machine transitions
        if best_candidate and self._is_line_crossed(best_candidate):
            # Direct crossing detected
            self.state = CounterState.COUNTED
            self.departures += 1
            self._last_count_time = now
            self.state = CounterState.COOLDOWN

            event = self._make_crossing_event(best_candidate, frame_number)
            crossing_events.append(event)
            self._crossing_events.append(event)

        self._update_centroids(detections)
        return crossing_events

    def _is_line_crossed(self, det: dict) -> bool:
        """Check if this detection crossed the counting line since last frame."""
        track_id = det["track_id"]
        cx, cy = det["centroid"]
        if track_id in self._prev_centroids:
            _, prev_cy = self._prev_centroids[track_id]
            return self.counting_line.is_crossing(prev_cy, cy)
        return False

    def _update_centroids(self, detections: list[dict]) -> None:
        """Update centroid history for all tracks."""
        current_ids = set()
        for det in detections:
            tid = det["track_id"]
            self._prev_centroids[tid] = det["centroid"]
            current_ids.add(tid)
        # Clean up old tracks (keep last 100)
        if len(self._prev_centroids) > 100:
            old_keys = list(self._prev_centroids.keys())
            for k in old_keys:
                if k not in current_ids:
                    del self._prev_centroids[k]
                    if len(self._prev_centroids) <= 50:
                        break

    def _make_crossing_event(self, det: dict, frame_number: int) -> dict:
        """Create a crossing event dict from a detection."""
        x1, y1, x2, y2 = det["bbox"]
        cx, cy = det["centroid"]
        return {
            "camera_id": self.camera_id,
            "track_id": det["track_id"],
            "object_class_raw": det["class_name"],
            "confidence": det["confidence"],
            "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
            "centroid": {"x": cx, "y": cy},
            "direction": "outbound",
            "frame_number": frame_number,
            "departures": self.departures,
            "arrivals": self.arrivals,
        }

    def reset(self) -> None:
        """Reset all counts and state."""
        self.state = CounterState.IDLE
        self.departures = 0
        self.arrivals = 0
        self._entering_frames = 0
        self._last_count_time = 0.0
        self._prev_centroids.clear()
        self._crossing_events.clear()
