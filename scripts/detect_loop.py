"""GPU detection loop — runs inside the Ultralytics Jetson Docker container.

Grabs frames from RTSP cameras, runs YOLO detection, writes annotated
snapshots + detections.json for the dashboard server to serve.

Prefers a fine-tuned model (box-detector-finetuned.pt) if available —
6MB YOLOv8n loads in <2s and runs at 60+ FPS vs YOLO-World's 1.2GB
and 60s cold start. Falls back to YOLO-World for zero-shot detection.

Mounted into the container at /root/detect_loop.py via the Expanso job spec.
"""

import json
import os
import sys
import time

import cv2

os.environ["YOLO_VERBOSE"] = "false"
from ultralytics import YOLO  # noqa: E402

# Load camera credentials from .env
with open("/data/.env") as f:
    env = {}
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v

# Prefer fine-tuned model (tiny, fast) over YOLO-World (huge, slow)
FINETUNED_PATH = "/data/box-detector-finetuned.pt"
if os.path.exists(FINETUNED_PATH):
    print(f"Using fine-tuned model: {FINETUNED_PATH}", flush=True)
    model = YOLO(FINETUNED_PATH)
    CONF = 0.25  # Fine-tuned models are confident — higher threshold
else:
    print("No fine-tuned model found, using YOLO-World (slow)", flush=True)
    model = YOLO("yolov8s-worldv2.pt")
    model.set_classes(
        [
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
    )
    CONF = 0.08  # YOLO-World needs low conf

cameras = {
    "cam-outside": "rtsp://{}:{}@{}:554/h264Preview_01_sub".format(
        env["CAM_USER"], env["CAM_PASS_OUTSIDE"], env["CAM_IP_OUTSIDE"]
    ),
    "cam-inside": "rtsp://{}:{}@{}:554/h264Preview_01_sub".format(
        env["CAM_USER"], env["CAM_PASS_INSIDE"], env["CAM_IP_INSIDE"]
    ),
}

os.makedirs("/data/snapshots", exist_ok=True)

while True:
    detections = {}
    for cam_id, url in cameras.items():
        cap = cv2.VideoCapture(url)
        for _ in range(3):
            cap.read()
        ret, frame = cap.read()
        cap.release()
        if not ret:
            detections[cam_id] = {"boxes": 0, "status": "offline"}
            continue

        results = model(frame, verbose=False, conf=CONF, iou=0.5)
        annotated = results[0].plot()
        cv2.imwrite(
            "/data/snapshots/{}.jpg".format(cam_id),
            annotated,
            [cv2.IMWRITE_JPEG_QUALITY, 80],
        )
        boxes = len(results[0].boxes) if results[0].boxes is not None else 0
        detections[cam_id] = {"boxes": boxes, "status": "online"}

    with open("/data/snapshots/detections.json", "w") as f:
        json.dump(detections, f)

    # Output for Expanso pipeline capture
    event = {
        "schema_version": "1.0.0",
        "event_type": "detection_scan",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "detections": detections,
    }
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()

    time.sleep(2)
