"""Main inference pipeline: RTSP ingest → YOLO → ByteTrack → Line Counter → Events.

This process is intentionally simple. It reads camera frames, runs detection
and tracking, counts line crossings, and writes raw events to an NDJSON file.
Expanso handles everything after that.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from expanso_security_camera.config import CameraConfig, DemoConfig
from expanso_security_camera.counter import (
    CounterStateMachine,
    CountingLine,
    DirectionFilter,
    SizeFilter,
)
from expanso_security_camera.events import (
    BoundingBox,
    Centroid,
    CrossingEvent,
    DashboardState,
    DetectionInfo,
    Direction,
    FrameInfo,
    ModelInfo,
    ReconciliationEvent,
    emit_event,
    write_state,
)

# COCO class names for mapping
COCO_NAMES = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    4: "airplane",
    5: "bus",
    6: "train",
    7: "truck",
    8: "boat",
    9: "traffic light",
    10: "fire hydrant",
    11: "stop sign",
    12: "parking meter",
    13: "bench",
    14: "bird",
    15: "cat",
    16: "dog",
    17: "horse",
    18: "sheep",
    19: "cow",
    20: "elephant",
    21: "bear",
    22: "zebra",
    23: "giraffe",
    24: "backpack",
    25: "umbrella",
    26: "handbag",
    27: "tie",
    28: "suitcase",
    29: "frisbee",
    30: "skis",
    31: "snowboard",
    32: "sports ball",
    33: "kite",
    34: "baseball bat",
    35: "baseball glove",
    36: "skateboard",
    37: "surfboard",
    38: "tennis racket",
    39: "bottle",
    40: "wine glass",
    41: "cup",
    42: "fork",
    43: "knife",
    44: "spoon",
    45: "bowl",
    46: "banana",
    47: "apple",
    48: "sandwich",
    49: "orange",
    50: "broccoli",
    51: "carrot",
    52: "hot dog",
    53: "pizza",
    54: "donut",
    55: "cake",
    56: "chair",
    57: "couch",
    58: "potted plant",
    59: "bed",
    60: "dining table",
    61: "toilet",
    62: "tv",
    63: "laptop",
    64: "mouse",
    65: "remote",
    66: "keyboard",
    67: "cell phone",
    68: "microwave",
    69: "oven",
    70: "toaster",
    71: "sink",
    72: "refrigerator",
    73: "book",
    74: "clock",
    75: "vase",
    76: "scissors",
    77: "teddy bear",
    78: "hair drier",
    79: "toothbrush",
}


class CameraThread:
    """Reads frames from a camera in a background thread."""

    def __init__(self, camera_config: CameraConfig):
        self.config = camera_config
        self.frame_queue: queue.Queue[tuple[float, np.ndarray, int]] = queue.Queue(maxsize=5)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_count = 0
        self._fps = 0.0
        self._connected = False

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._read_loop, name=f"cam-{self.config.camera_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def get_frame(self, timeout: float = 1.0) -> tuple[float, np.ndarray, int] | None:
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def fps(self) -> float:
        return self._fps

    def _read_loop(self) -> None:
        url = self.config.url
        # Support integer device index for USB cameras
        source = int(url) if url.isdigit() else url

        while not self._stop_event.is_set():
            try:
                cap = cv2.VideoCapture(source)
                if isinstance(source, str) and "rtsp" in source.lower():
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                if not cap.isOpened():
                    print(f"[{self.config.camera_id}] Cannot open {url}, retrying in 5s...")
                    time.sleep(5)
                    continue

                self._connected = True
                print(f"[{self.config.camera_id}] Connected to {url}")
                fps_timer = time.time()
                fps_count = 0

                while not self._stop_event.is_set():
                    ret, frame = cap.read()
                    if not ret:
                        print(f"[{self.config.camera_id}] Frame read failed, reconnecting...")
                        break

                    self._frame_count += 1
                    fps_count += 1

                    # Calculate FPS every second
                    elapsed = time.time() - fps_timer
                    if elapsed >= 1.0:
                        self._fps = fps_count / elapsed
                        fps_count = 0
                        fps_timer = time.time()

                    # Drop oldest frame if queue is full
                    if self.frame_queue.full():
                        try:
                            self.frame_queue.get_nowait()
                        except queue.Empty:
                            pass

                    self.frame_queue.put((time.time(), frame, self._frame_count))

                cap.release()
                self._connected = False

            except Exception as e:
                print(f"[{self.config.camera_id}] Error: {e}, reconnecting in 5s...")
                self._connected = False
                time.sleep(5)


def map_class_name(raw_class: str, class_map: dict[str, str], mode: str) -> str:
    """Map a raw COCO class name to a display name."""
    if raw_class in class_map:
        return class_map[raw_class]
    if mode == "person":
        return "person"
    return "box"


def log(msg: str) -> None:
    """Log to stderr so stdout stays clean for Expanso subprocess capture."""
    print(msg, file=sys.stderr, flush=True)


def check_commands(commands_path: str) -> str | None:
    """Check for dashboard commands (e.g., reset)."""
    if os.path.exists(commands_path):
        try:
            with open(commands_path) as f:
                cmd = json.load(f)
            os.remove(commands_path)
            return cmd.get("action")
        except (json.JSONDecodeError, OSError):
            pass
    return None


def run_pipeline(config: DemoConfig) -> None:
    """Main inference loop."""
    log("=" * 60)
    log("  Security Camera Box Counting — Inference Pipeline")
    log(f"  Device: {config.device_id}")
    log(f"  Mode: {config.detect_mode}")
    log(f"  Cameras: {len(config.cameras)}")
    log("=" * 60)

    # Load YOLO model
    log(f"\nLoading {config.model_name}...")
    model = YOLO(f"{config.model_name}.pt")

    # If using YOLO-World (open-vocabulary), set custom classes
    if "world" in config.model_name.lower():
        world_classes = ["cardboard box", "shipping box", "package", "person"]
        log(f"Setting YOLO-World classes: {world_classes}")
        model.set_classes(world_classes)

    log(f"Model loaded: {config.model_name}")

    # Set up camera threads
    camera_threads: dict[str, CameraThread] = {}
    for cam_config in config.cameras:
        ct = CameraThread(cam_config)
        camera_threads[cam_config.camera_id] = ct
        ct.start()

    # Set up counters
    counters: dict[str, CounterStateMachine] = {}
    for cam_config in config.cameras:
        line = CountingLine(
            y=cam_config.counting_line_y,
            x_start=cam_config.counting_line_x_start,
            x_end=cam_config.counting_line_x_end,
            count_direction=cam_config.count_direction,
        )
        direction = DirectionFilter(allowed_direction=cam_config.direction_filter)
        size = SizeFilter(
            min_area=config.size_filter_min_area,
            max_area=config.size_filter_max_area,
            min_width=config.size_filter_min_width,
        )
        counter = CounterStateMachine(
            camera_id=cam_config.camera_id,
            counting_line=line,
            direction_filter=direction,
            size_filter=size,
            cooldown_seconds=config.cooldown_seconds,
        )
        counters[cam_config.camera_id] = counter

    # Initialize state
    state = DashboardState(detect_mode=config.detect_mode)

    log("\nWaiting for camera streams...")
    time.sleep(2)
    log("Starting inference loop. Press Ctrl+C to stop.\n")

    frame_count = 0
    fps_timer = time.time()
    fps_count = 0

    try:
        while True:
            # Check for commands
            action = check_commands(config.commands_path)
            if action == "reset":
                log("\n>>> SESSION RESET <<<\n")
                for counter in counters.values():
                    counter.reset()
                state = DashboardState(detect_mode=config.detect_mode)
                write_state(config.state_path, state)
                continue

            # Grab frames from all cameras
            frames: dict[str, tuple[float, np.ndarray, int]] = {}
            for cam_id, ct in camera_threads.items():
                result = ct.get_frame(timeout=0.5)
                if result:
                    frames[cam_id] = result

            if not frames:
                continue

            frame_count += 1
            fps_count += 1

            # Skip frames based on inference interval
            if frame_count % config.inference_interval != 0:
                continue

            # Run inference on each frame
            for cam_id, (ts, frame, cam_frame_num) in frames.items():
                cam_config = next(c for c in config.cameras if c.camera_id == cam_id)
                counter = counters[cam_id]

                # Run YOLO with tracking
                inference_start = time.time()
                results = model.track(
                    frame,
                    verbose=False,
                    persist=True,
                    tracker="bytetrack.yaml",
                    conf=config.confidence_threshold,
                    classes=config.detect_classes,
                )
                inference_ms = (time.time() - inference_start) * 1000

                # Extract detections
                detections = []
                if results and results[0].boxes is not None:
                    for box in results[0].boxes:
                        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                        cx = (x1 + x2) / 2
                        cy = (y1 + y2) / 2
                        cls_id = int(box.cls[0])
                        conf = float(box.conf[0])
                        track_id = int(box.id[0]) if box.id is not None else -1
                        raw_class = COCO_NAMES.get(cls_id, f"class_{cls_id}")

                        detections.append(
                            {
                                "track_id": track_id,
                                "class_name": raw_class,
                                "class_id": cls_id,
                                "confidence": conf,
                                "bbox": (x1, y1, x2, y2),
                                "centroid": (cx, cy),
                            }
                        )

                # Update counter
                crossing_events = counter.update(detections, cam_frame_num)

                # Emit crossing events
                for evt_data in crossing_events:
                    raw_class = evt_data["object_class_raw"]
                    mapped_class = map_class_name(raw_class, config.class_map, config.detect_mode)

                    event = CrossingEvent(
                        camera_id=cam_id,
                        detection=DetectionInfo(
                            track_id=evt_data["track_id"],
                            object_class=mapped_class,
                            object_class_raw=raw_class,
                            confidence=evt_data["confidence"],
                            bounding_box=BoundingBox(**evt_data["bbox"]),
                            centroid=Centroid(**evt_data["centroid"]),
                            direction=Direction.OUTBOUND
                            if cam_config.role == "outside"
                            else Direction.INBOUND,
                            crossing_line_id=f"{cam_id}-line-1",
                        ),
                        model=ModelInfo(
                            model_name=config.model_name,
                            precision=config.model_precision,
                            inference_time_ms=round(inference_ms, 1),
                        ),
                        frame=FrameInfo(
                            frame_number=cam_frame_num,
                            frame_timestamp=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        ),
                        counts={
                            "camera_departures": evt_data["departures"],
                            "camera_arrivals": evt_data["arrivals"],
                        },
                    )
                    emit_event(event)

                    # Log
                    direction_str = "departed" if cam_config.role == "outside" else "arrived"
                    count = (
                        evt_data["departures"]
                        if cam_config.role == "outside"
                        else evt_data["arrivals"]
                    )
                    now_str = datetime.now().strftime("%I:%M:%S %p")
                    log(
                        f"  {now_str}  {cam_id}  "
                        f"{mapped_class.capitalize()} {direction_str}  "
                        f"(count: {count})"
                    )

            # Update dashboard state
            outside_counter = None
            inside_counter = None
            for cam_config in config.cameras:
                counter = counters[cam_config.camera_id]
                if cam_config.role == "outside":
                    outside_counter = counter
                elif cam_config.role == "inside":
                    inside_counter = counter

            if outside_counter and inside_counter:
                dep = outside_counter.departures
                arr = inside_counter.departures  # both count "departures" in their direction
                disc = dep - arr
                status = "MATCH" if disc == 0 else "DISCREPANCY"

                if disc != 0 and state.first_discrepancy_at is None:
                    state.first_discrepancy_at = datetime.now(timezone.utc).isoformat()

                state.camera_outside_departures = dep
                state.camera_inside_arrivals = arr
                state.discrepancy = disc
                state.status = status
                state.last_event_ts = datetime.now(timezone.utc).isoformat()

                # Keep last 50 events for the dashboard log
                for cam_id in frames:
                    cam_counter = counters[cam_id]
                    for evt in cam_counter._crossing_events[len(state.recent_events) :]:
                        state.recent_events.append(evt)
                state.recent_events = state.recent_events[-50:]

                # Calculate FPS
                elapsed = time.time() - fps_timer
                if elapsed >= 2.0:
                    state.inference_fps = round(fps_count / elapsed, 1)
                    fps_count = 0
                    fps_timer = time.time()

                # Write reconciliation event if discrepancy changed
                if disc != 0:
                    recon = ReconciliationEvent(
                        camera_outside_id=(outside_counter.camera_id),
                        camera_inside_id=inside_counter.camera_id,
                        outside_departures=dep,
                        inside_arrivals=arr,
                        discrepancy=disc,
                        status=status,
                        first_discrepancy_at=state.first_discrepancy_at,
                        session_id=state.session_id,
                    )
                    emit_event(recon)

                write_state(config.state_path, state)

    except KeyboardInterrupt:
        log("\n\nShutting down...")
    finally:
        for ct in camera_threads.values():
            ct.stop()
        log("Pipeline stopped.")


def main() -> None:
    """Entry point for esc-infer command."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else None

    if config_path and Path(config_path).exists():
        config = DemoConfig.from_yaml(config_path)
    else:
        config = DemoConfig.default_dock_door()
        if not config_path:
            log("No config file specified. Using default dock door config.")
            log("Edit config.yaml or pass a config file as argument.\n")

    run_pipeline(config)


if __name__ == "__main__":
    main()
