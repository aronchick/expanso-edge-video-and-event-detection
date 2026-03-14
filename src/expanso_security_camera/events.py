"""Event schemas for the security camera box counting pipeline.

Defines the structured events emitted by the inference process.
These are raw events — Expanso enriches them with metadata downstream.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Direction(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class BoundingBox(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int


class Centroid(BaseModel):
    x: float
    y: float


class DetectionInfo(BaseModel):
    track_id: int
    object_class: str  # mapped class (e.g., "box")
    object_class_raw: str  # raw COCO class (e.g., "suitcase")
    confidence: float
    bounding_box: BoundingBox
    centroid: Centroid
    direction: Direction
    crossing_line_id: str


class ModelInfo(BaseModel):
    model_name: str = "yolov8s"
    model_version: str = "8.1.0"
    precision: str = "fp16"
    engine: str = "tensorrt"
    input_resolution: str = "640x640"
    inference_time_ms: float = 0.0


class FrameInfo(BaseModel):
    frame_number: int
    frame_timestamp: str
    resolution: str = "1280x720"


class CrossingEvent(BaseModel):
    schema_version: str = "1.0.0"
    event_type: str = "crossing"
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    camera_id: str
    detection: DetectionInfo
    model: ModelInfo = Field(default_factory=ModelInfo)
    frame: FrameInfo

    counts: dict[str, int] = Field(default_factory=dict)


class WindowedCountEvent(BaseModel):
    schema_version: str = "1.0.0"
    event_type: str = "windowed_count"
    event_id: str = Field(default_factory=lambda: f"wnd_{uuid.uuid4().hex[:12]}")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    camera_id: str
    window_start: str
    window_end: str
    window_duration_seconds: int = 30

    departures: int = 0
    arrivals: int = 0
    unique_tracks: int = 0

    session_total_departures: int = 0
    session_total_arrivals: int = 0

    avg_confidence: float = 0.0
    min_confidence: float = 1.0
    inference_fps: float = 0.0


class ReconciliationEvent(BaseModel):
    schema_version: str = "1.0.0"
    event_type: str = "reconciliation"
    event_id: str = Field(default_factory=lambda: f"rec_{uuid.uuid4().hex[:12]}")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    camera_outside_id: str
    camera_inside_id: str

    outside_departures: int
    inside_arrivals: int
    discrepancy: int
    status: str  # "MATCH" or "DISCREPANCY"
    first_discrepancy_at: str | None = None

    session_id: str = ""


class DiscrepancyAlert(BaseModel):
    schema_version: str = "1.0.0"
    event_type: str = "discrepancy_alert"
    event_id: str = Field(default_factory=lambda: f"alert_{uuid.uuid4().hex[:12]}")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str = "warning"

    message: str
    outside_count: int
    inside_count: int
    missing_count: int
    first_detected: str
    session_id: str = ""


class DashboardState(BaseModel):
    """Shared state written to state.json for the dashboard to read."""

    session_id: str = Field(
        default_factory=lambda: (f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    )
    camera_outside_departures: int = 0
    camera_inside_arrivals: int = 0
    discrepancy: int = 0
    status: str = "MATCH"
    last_event_ts: str = ""
    first_discrepancy_at: str | None = None
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    inference_fps: float = 0.0
    pipeline_status: str = "running"
    detect_mode: str = "box"  # "box" or "person"


def emit_event(event: BaseModel) -> None:
    """Emit a JSON event to stdout for Expanso subprocess capture.

    This is the primary output path. Expanso Edge reads our stdout
    as its input stream when we run as a subprocess.
    Flush immediately so Expanso gets events in real-time.
    """
    sys.stdout.write(event.model_dump_json() + "\n")
    sys.stdout.flush()


def write_state(filepath: str, state: DashboardState) -> None:
    """Write dashboard state atomically (write tmp then rename)."""
    import os

    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w") as f:
        f.write(state.model_dump_json(indent=2))
    os.replace(tmp_path, filepath)
