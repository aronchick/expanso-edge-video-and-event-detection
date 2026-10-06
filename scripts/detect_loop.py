"""GPU detection loop for the box-counting variant.

Runs inside the Ultralytics Jetson container (see jobs/yolo-detector-job.yaml).
Every few seconds it grabs a frame from each camera, runs YOLO, writes an
annotated snapshot plus detections.json for the dashboard, and prints one
`detection_scan` JSON line on stdout for the Expanso pipeline. Diagnostics go
to stderr.

It prefers the fine-tuned box detector (6 MB, ~60 FPS) and falls back to the
YOLO-World open-vocabulary model.

Configuration, all optional except where noted:
  DETECT_DATA_DIR     where snapshots and .env live          (default /data)
  DETECT_ENV_FILE     camera credentials file                 (default <data>/.env)
  CAM_URL_OUTSIDE     full RTSP URL or recorded video file    (else built from CAM_*)
  CAM_URL_INSIDE
  DETECT_MODEL        weights to load instead of the default choice
  DETECT_INTERVAL     seconds between scans                   (default 2)
  DETECT_FRAME_STEP   frames to skip between scans of a recorded file (default 15)

A recorded file is read once from start to end and the loop then exits, so the
same script serves as the local replay of the pipeline's input.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

FINETUNED_NAME = "box-detector-finetuned.pt"
WORLD_CLASSES = [
    "cardboard box",
    "shipping box",
    "package",
    "carton",
    "box",
    "parcel",
    "crate",
    "container",
    "brown box",
    "sealed box",
    "stacked boxes",
    "rectangular object",
    "delivery package",
    "moving box",
]


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def parse_env_file(path: str | Path) -> dict[str, str]:
    """KEY=value lines; comments and blanks skipped; values may contain '='."""
    env: dict[str, str] = {}
    file = Path(path)

    if not file.is_file():
        return env

    for line in file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.strip().split("=", 1)
            env[key] = value

    return env


def camera_sources(env: dict[str, str]) -> dict[str, str]:
    """Camera id to RTSP URL or recorded file. A full CAM_URL_* wins over the
    credentials-and-address form."""
    sources: dict[str, str] = {}

    for cam_id, suffix in (("cam-outside", "OUTSIDE"), ("cam-inside", "INSIDE")):
        url = env.get(f"CAM_URL_{suffix}")

        if not url and env.get(f"CAM_IP_{suffix}"):
            url = "rtsp://{}:{}@{}:554/h264Preview_01_sub".format(
                env["CAM_USER"], env[f"CAM_PASS_{suffix}"], env[f"CAM_IP_{suffix}"]
            )

        if url:
            sources[cam_id] = url

    return sources


def choose_model(data_dir: Path, override: str | None) -> tuple[str, float, bool]:
    """(weights, confidence threshold, is_open_vocabulary)."""
    if override:
        world = "world" in override.lower()

        return override, 0.08 if world else 0.25, world

    finetuned = data_dir / FINETUNED_NAME

    if finetuned.exists():
        return str(finetuned), 0.25, False

    return "yolov8s-worldv2.pt", 0.08, True


def scan_event(detections: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "event_type": "detection_scan",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "detections": detections,
    }


def main() -> int:
    import cv2

    os.environ["YOLO_VERBOSE"] = "false"
    from ultralytics import YOLO

    data_dir = Path(os.environ.get("DETECT_DATA_DIR", "/data"))
    env = {
        **parse_env_file(os.environ.get("DETECT_ENV_FILE", data_dir / ".env")),
        **{k: v for k, v in os.environ.items() if k.startswith(("CAM_", "DETECT_"))},
    }
    sources = camera_sources(env)

    if not sources:
        log("no cameras configured: set CAM_URL_OUTSIDE/CAM_URL_INSIDE or CAM_USER and CAM_*")
        return 2

    weights, conf, world = choose_model(data_dir, env.get("DETECT_MODEL"))
    log(f"model: {weights} (conf {conf})")
    model = YOLO(weights)

    if world:
        model.set_classes(WORLD_CLASSES)

    interval = float(env.get("DETECT_INTERVAL", "2"))
    step = int(env.get("DETECT_FRAME_STEP", "15"))
    snapshots = data_dir / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    files = {cam: cv2.VideoCapture(url) for cam, url in sources.items() if os.path.isfile(url)}
    finished: set[str] = set()

    while True:
        detections: dict[str, dict] = {}

        for cam_id, url in sources.items():
            if cam_id in files:
                cap = files[cam_id]
                ret, frame = cap.read()

                for _ in range(step):
                    cap.grab()

                if not ret:
                    finished.add(cam_id)
                    detections[cam_id] = {"boxes": 0, "status": "offline"}
                    continue
            else:
                cap = cv2.VideoCapture(url)

                for _ in range(3):
                    cap.read()

                ret, frame = cap.read()
                cap.release()

                if not ret:
                    detections[cam_id] = {"boxes": 0, "status": "offline"}
                    continue

            results = model(frame, verbose=False, conf=conf, iou=0.5)
            annotated = results[0].plot()
            cv2.imwrite(str(snapshots / f"{cam_id}.jpg"), annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
            boxes = len(results[0].boxes) if results[0].boxes is not None else 0
            detections[cam_id] = {"boxes": boxes, "status": "online"}

        (snapshots / "detections.json").write_text(json.dumps(detections), encoding="utf-8")

        if files and finished >= set(files):
            log("recordings finished")
            return 0

        sys.stdout.write(json.dumps(scan_event(detections)) + "\n")
        sys.stdout.flush()
        time.sleep(interval if not files else 0)


if __name__ == "__main__":
    raise SystemExit(main())
