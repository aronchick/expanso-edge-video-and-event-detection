"""Video recorder — captures RTSP streams to segmented video files.

This runs as a separate Expanso subprocess alongside the inference engine.
It reads from the same cameras but writes video segments to disk for
evidence/replay, rather than running inference.

Segments are timestamped and rotatable (e.g., 5-minute chunks).
When a discrepancy is detected, the relevant segment can be pulled
for visual verification.

Run via Expanso pipeline:
    expanso-edge run pipelines/demo/security-camera-recorder.yaml

Or standalone for testing:
    uv run esc-record config.yaml

Output format (NDJSON to stdout for Expanso):
    {"event_type": "recording_segment", "camera_id": "cam-outside",
     "path": "recordings/cam-outside/2026-03-15_14-47-00.mp4",
     "start_time": "...", "end_time": "...", "frames": 450, "size_bytes": 12345}
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

from expanso_security_camera.config import CameraConfig, DemoConfig


def log(msg: str) -> None:
    """Log to stderr so stdout stays clean for Expanso."""
    print(msg, file=sys.stderr, flush=True)


def emit(event: dict) -> None:
    """Emit recording metadata event to stdout for Expanso."""
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


class SegmentRecorder:
    """Records RTSP stream to segmented MP4 files."""

    def __init__(
        self,
        camera_config: CameraConfig,
        output_dir: str = "recordings",
        segment_duration_seconds: int = 300,  # 5-minute segments
    ):
        self.config = camera_config
        self.output_dir = Path(output_dir) / camera_config.camera_id
        self.segment_duration = segment_duration_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._record_loop,
            name=f"rec-{self.config.camera_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _record_loop(self) -> None:
        url = self.config.url
        source = int(url) if url.isdigit() else url

        while not self._stop_event.is_set():
            try:
                cap = cv2.VideoCapture(source)
                if isinstance(source, str) and "rtsp" in source.lower():
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                if not cap.isOpened():
                    log(f"[{self.config.camera_id}] Cannot open {url}, retrying in 5s...")
                    time.sleep(5)
                    continue

                fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)

                log(
                    f"[{self.config.camera_id}] Recording: "
                    f"{width}x{height} @ {fps:.0f}fps, "
                    f"{self.segment_duration}s segments"
                )

                while not self._stop_event.is_set():
                    self._record_segment(cap, fps, width, height)

                cap.release()

            except Exception as e:
                log(f"[{self.config.camera_id}] Recording error: {e}, retrying in 5s...")
                time.sleep(5)

    def _record_segment(
        self,
        cap: cv2.VideoCapture,
        fps: float,
        width: int,
        height: int,
    ) -> None:
        """Record one segment of video."""
        now = datetime.now(timezone.utc)
        filename = now.strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
        filepath = self.output_dir / filename

        fourcc = cv2.VideoWriter.fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(filepath), fourcc, fps, (width, height))

        start_time = now
        frame_count = 0
        segment_start = time.time()

        try:
            while not self._stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    log(f"[{self.config.camera_id}] Frame read failed during recording")
                    break

                writer.write(frame)
                frame_count += 1

                elapsed = time.time() - segment_start
                if elapsed >= self.segment_duration:
                    break

        finally:
            writer.release()
            end_time = datetime.now(timezone.utc)
            size_bytes = filepath.stat().st_size if filepath.exists() else 0

            if frame_count > 0:
                log(
                    f"[{self.config.camera_id}] Segment: {filename} "
                    f"({frame_count} frames, {size_bytes / 1024:.0f} KB)"
                )

                emit(
                    {
                        "schema_version": "1.0.0",
                        "event_type": "recording_segment",
                        "camera_id": self.config.camera_id,
                        "path": str(filepath),
                        "filename": filename,
                        "start_time": start_time.isoformat(),
                        "end_time": end_time.isoformat(),
                        "frames": frame_count,
                        "size_bytes": size_bytes,
                        "resolution": f"{width}x{height}",
                        "fps": fps,
                    }
                )


def run_recorder(config: DemoConfig) -> None:
    """Main recording loop — records all configured cameras."""
    log("=" * 60)
    log("  Security Camera Recorder")
    log(f"  Cameras: {len(config.cameras)}")
    log("=" * 60)

    recorders = []
    for cam_config in config.cameras:
        # Use main stream (4K) for recording if available
        # Replace sub-stream URL with main stream URL for recording
        record_config = CameraConfig(
            camera_id=cam_config.camera_id,
            url=cam_config.url.replace("_01_sub", "_01_main"),
            counting_line_y=cam_config.counting_line_y,
            counting_line_x_start=cam_config.counting_line_x_start,
            counting_line_x_end=cam_config.counting_line_x_end,
            count_direction=cam_config.count_direction,
            direction_filter=cam_config.direction_filter,
            role=cam_config.role,
        )

        segment_secs = int(os.environ.get("SEGMENT_DURATION", "300"))
        output_dir = os.environ.get("RECORDING_DIR", "recordings")

        recorder = SegmentRecorder(
            record_config,
            output_dir=output_dir,
            segment_duration_seconds=segment_secs,
        )
        recorders.append(recorder)
        recorder.start()

    log("\nRecording started. Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("\nStopping recorders...")
    finally:
        for r in recorders:
            r.stop()
        log("Recording stopped.")


def main() -> None:
    """Entry point for esc-record command."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else None

    if config_path and Path(config_path).exists():
        config = DemoConfig.from_yaml(config_path)
    else:
        config = DemoConfig.default_dock_door()
        log("No config file specified. Using default dock door config.")

    run_recorder(config)


if __name__ == "__main__":
    main()
