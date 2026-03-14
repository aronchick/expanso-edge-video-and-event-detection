"""Configuration for the security camera demo."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CameraConfig:
    camera_id: str
    url: str  # RTSP URL or device index or video file path
    counting_line_y: int = 350  # y-coordinate of counting line
    counting_line_x_start: int = 50
    counting_line_x_end: int = 1200
    count_direction: str = "down"  # "down" or "up"
    direction_filter: str = "right"  # "right" or "left" — which x-direction to count
    role: str = "outside"  # "outside" (departures) or "inside" (arrivals)


@dataclass
class DemoConfig:
    device_id: str = "recomputer-j3011-001"
    site_id: str = "warehouse-demo"

    cameras: list[CameraConfig] = field(default_factory=list)

    model_name: str = "yolov8s"
    model_precision: str = "fp16"
    confidence_threshold: float = 0.3
    detect_classes: list[int] | None = None  # None = all classes
    detect_mode: str = "box"  # "box" or "person"

    # class mapping: raw COCO class name -> display name
    class_map: dict[str, str] = field(default_factory=dict)

    # counting
    cooldown_seconds: float = 3.0
    size_filter_min_area: int = 3000
    size_filter_max_area: int = 200000
    size_filter_min_width: int = 40

    # paths
    raw_events_path: str = "raw-events.ndjson"
    state_path: str = "state.json"
    commands_path: str = "commands.json"
    archive_path: str = "enriched-events.ndjson"

    # inference
    inference_interval: int = 1  # run inference every Nth frame
    batch_size: int = 2

    # dashboard
    dashboard_port: int = 8501

    @classmethod
    def from_yaml(cls, path: str | Path) -> DemoConfig:
        with open(path) as f:
            data = yaml.safe_load(f)

        cameras = []
        for cam_data in data.get("cameras", []):
            cameras.append(CameraConfig(**cam_data))

        config = cls(
            device_id=data.get("device_id", cls.device_id),
            site_id=data.get("site_id", cls.site_id),
            cameras=cameras,
            model_name=data.get("model_name", cls.model_name),
            model_precision=data.get("model_precision", cls.model_precision),
            confidence_threshold=data.get("confidence_threshold", cls.confidence_threshold),
            detect_classes=data.get("detect_classes"),
            detect_mode=data.get("detect_mode", cls.detect_mode),
            class_map=data.get("class_map", {}),
            cooldown_seconds=data.get("cooldown_seconds", cls.cooldown_seconds),
            size_filter_min_area=data.get("size_filter_min_area", cls.size_filter_min_area),
            size_filter_max_area=data.get("size_filter_max_area", cls.size_filter_max_area),
            size_filter_min_width=data.get("size_filter_min_width", cls.size_filter_min_width),
            raw_events_path=data.get("raw_events_path", cls.raw_events_path),
            state_path=data.get("state_path", cls.state_path),
            commands_path=data.get("commands_path", cls.commands_path),
            archive_path=data.get("archive_path", cls.archive_path),
            inference_interval=data.get("inference_interval", cls.inference_interval),
        )
        return config

    @classmethod
    def default_dock_door(cls) -> DemoConfig:
        """Pre-configured for the dock door box counting demo."""
        return cls(
            cameras=[
                CameraConfig(
                    camera_id="cam-outside",
                    url="rtsp://192.168.1.100:554/h264Preview_01_sub",
                    counting_line_y=350,
                    count_direction="down",
                    direction_filter="right",
                    role="outside",
                ),
                CameraConfig(
                    camera_id="cam-inside",
                    url="rtsp://192.168.1.101:554/h264Preview_01_sub",
                    counting_line_y=350,
                    count_direction="down",
                    direction_filter="left",
                    role="inside",
                ),
            ],
            detect_mode="box",
            class_map={
                "suitcase": "box",
                "backpack": "box",
                "handbag": "box",
                "sports ball": "box",
                "bottle": "box",
            },
        )

    @classmethod
    def default_people_counting(cls) -> DemoConfig:
        """Pre-configured for people counting demo."""
        return cls(
            cameras=[
                CameraConfig(
                    camera_id="cam-entrance-a",
                    url="rtsp://192.168.1.100:554/h264Preview_01_sub",
                    counting_line_y=350,
                    count_direction="down",
                    direction_filter="right",
                    role="outside",
                ),
                CameraConfig(
                    camera_id="cam-entrance-b",
                    url="rtsp://192.168.1.101:554/h264Preview_01_sub",
                    counting_line_y=350,
                    count_direction="down",
                    direction_filter="left",
                    role="inside",
                ),
            ],
            detect_mode="person",
            detect_classes=[0],  # COCO class 0 = person
        )
