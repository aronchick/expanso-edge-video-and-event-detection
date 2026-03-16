"""Shared fixtures for all tests."""

from __future__ import annotations

import json

import numpy as np
import pytest


@pytest.fixture
def sample_frame():
    """640x360 BGR frame with some structure (not just black)."""
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    # Add some rectangles to simulate boxes
    frame[50:150, 50:150] = [180, 160, 130]  # brown box 1
    frame[50:150, 200:300] = [170, 150, 120]  # brown box 2
    frame[180:280, 50:150] = [190, 170, 140]  # brown box 3
    frame[180:280, 200:300] = [175, 155, 125]  # brown box 4
    return frame


@pytest.fixture
def sample_detections():
    """List of detection dicts in the format counter.py expects."""
    return [
        {
            "track_id": 1,
            "class_name": "suitcase",
            "class_id": 28,
            "confidence": 0.85,
            "bbox": (100, 100, 200, 250),
            "centroid": (150.0, 175.0),
        },
        {
            "track_id": 2,
            "class_name": "backpack",
            "class_id": 24,
            "confidence": 0.72,
            "bbox": (300, 150, 400, 300),
            "centroid": (350.0, 225.0),
        },
    ]


@pytest.fixture
def sample_config_yaml(tmp_path):
    """Write a minimal config.yaml and return its path."""
    config = {
        "device_id": "test-device",
        "site_id": "test-site",
        "detect_mode": "box",
        "model_name": "yolov8s",
        "model_precision": "fp16",
        "confidence_threshold": 0.3,
        "class_map": {"suitcase": "box", "backpack": "box"},
        "cameras": [
            {
                "camera_id": "cam-outside",
                "url": "rtsp://test:pass@192.168.1.1:554/stream",
                "counting_line_y": 180,
                "counting_line_x_start": 20,
                "counting_line_x_end": 620,
                "count_direction": "down",
                "direction_filter": "right",
                "role": "outside",
            },
            {
                "camera_id": "cam-inside",
                "url": "rtsp://test:pass@192.168.1.2:554/stream",
                "counting_line_y": 180,
                "counting_line_x_start": 20,
                "counting_line_x_end": 620,
                "count_direction": "down",
                "direction_filter": "left",
                "role": "inside",
            },
        ],
        "cooldown_seconds": 3.0,
        "size_filter_min_area": 1500,
        "size_filter_max_area": 200000,
        "size_filter_min_width": 30,
        "inference_interval": 1,
        "state_path": str(tmp_path / "state.json"),
        "commands_path": str(tmp_path / "commands.json"),
    }
    config_path = tmp_path / "config.yaml"
    import yaml

    config_path.write_text(yaml.dump(config))
    return str(config_path)


@pytest.fixture
def state_json(tmp_path):
    """Write a sample state.json and return its path."""
    state = {
        "session_id": "sess_20260316_120000",
        "camera_outside_departures": 5,
        "camera_inside_arrivals": 4,
        "discrepancy": 1,
        "status": "DISCREPANCY",
        "last_event_ts": "2026-03-16T12:00:00Z",
        "first_discrepancy_at": "2026-03-16T11:55:00Z",
        "recent_events": [],
        "inference_fps": 29.5,
        "pipeline_status": "running",
        "detect_mode": "box",
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    return path


@pytest.fixture
def detections_json(tmp_path):
    """Write a sample detections.json and return its path."""
    data = {
        "cam-outside": {"boxes": 5, "status": "online"},
        "cam-inside": {"boxes": 3, "status": "online"},
    }
    path = tmp_path / "detections.json"
    path.write_text(json.dumps(data))
    return path
