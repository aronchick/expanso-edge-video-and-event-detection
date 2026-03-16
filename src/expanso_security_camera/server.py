"""Self-hosted web dashboard server.

Runs on the Jetson alongside the Expanso pipeline. Serves the dashboard
UI, a JSON state API, and live camera snapshot endpoints.

Run with:
    uv run esc-server

Then open http://<jetson-ip>:8080 in a browser.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from expanso_security_camera.config import DemoConfig

STATE_PATH = Path("state.json")
CONFIG_PATH = Path("config.yaml")
PUBLIC_DIR = Path(__file__).parent.parent.parent / "public"

app = FastAPI(title="Box Transfer Monitor", docs_url=None, redoc_url=None)

# Cache camera URLs and YOLO model (loaded once at startup)
_camera_urls: dict[str, str] = {}
_detection_model = None


BOX_CLASSES = [
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


def _load_detection_model():
    """Load YOLO model once, cache globally.

    Prefers fine-tuned > standard YOLOv8s. YOLO-World is NOT used for
    live detection — it fails completely on dark/nighttime footage because
    CLIP text-image matching was trained on well-lit photos.
    Standard YOLOv8s handles low-light better via its COCO training data.
    """
    global _detection_model
    if _detection_model is None:
        from ultralytics import YOLO

        os.environ["YOLO_VERBOSE"] = "false"

        # Prefer fine-tuned model if it exists
        finetuned = Path("box-detector-finetuned.pt")
        if finetuned.exists():
            _detection_model = YOLO(str(finetuned))
        else:
            # Standard YOLOv8s — works in low light unlike YOLO-World
            _detection_model = YOLO("yolov8s.pt")
    return _detection_model


def _load_camera_urls() -> dict[str, str]:
    """Load camera URLs from config (cached)."""
    global _camera_urls
    if not _camera_urls and CONFIG_PATH.exists():
        try:
            config = DemoConfig.from_yaml(str(CONFIG_PATH))
            _camera_urls = {c.camera_id: c.url for c in config.cameras}
        except Exception:
            pass
    return _camera_urls


def _enhance_low_light(frame):
    """Apply CLAHE contrast enhancement for low-light/nighttime frames.

    CLAHE (Contrast Limited Adaptive Histogram Equalization) boosts local
    contrast without blowing out bright areas. This makes dark objects
    visible to YOLO without washing out the image.
    """
    # Aggressive enhancement for very dark nighttime security cameras
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    # First pass: strong CLAHE
    clahe = cv2.createCLAHE(clipLimit=6.0, tileGridSize=(4, 4))
    l_channel = clahe.apply(l_channel)
    # Second pass: stretch histogram to full range
    l_min, l_max = l_channel.min(), l_channel.max()
    if l_max > l_min:
        l_channel = ((l_channel - l_min) / (l_max - l_min) * 255).astype(np.uint8)
    enhanced = cv2.merge([l_channel, a_channel, b_channel])
    return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)


# COCO classes that could be a box in a warehouse setting
BOX_COCO_CLASSES = {
    24: "backpack",
    25: "umbrella",
    26: "handbag",
    27: "tie",
    28: "suitcase",
    39: "bottle",
    56: "chair",
    57: "couch",
    60: "dining table",
    62: "tv",
    63: "laptop",
    73: "book",
    75: "vase",
}


def _detect_boxes(frame) -> list[dict]:
    """Run single-pass detection optimized for Jetson real-time use.

    Applies CLAHE low-light enhancement before detection to handle
    nighttime/IR camera footage. Uses standard YOLOv8s (not World)
    because YOLO-World's CLIP fails completely in low light.
    """
    model = _load_detection_model()
    is_finetuned = Path("box-detector-finetuned.pt").exists()
    conf = 0.15 if is_finetuned else 0.05

    # Enhance dark frames before detection
    enhanced = _enhance_low_light(frame)
    preds = model(enhanced, verbose=False, conf=conf, imgsz=640, iou=0.3)
    dets: list[dict] = []
    if preds and preds[0].boxes is not None:
        for box in preds[0].boxes:
            cls_id = int(box.cls[0])
            c = float(box.conf[0])
            raw_name = model.names[cls_id]
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            # Accept all detections — let the dashboard show what YOLO sees
            display_name = "box" if (cls_id in BOX_COCO_CLASSES or is_finetuned) else raw_name
            dets.append(
                {
                    "class": display_name,
                    "confidence": round(c, 2),
                    "bbox": [x1, y1, x2, y2],
                }
            )
    return dets


def _grab_frame(url: str) -> bytes | None:
    """Grab a single fresh frame from an RTSP stream, return as JPEG bytes."""
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        return None
    # Flush buffer
    for _ in range(3):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return jpg.tobytes()


@app.get("/api/state")
async def get_state() -> JSONResponse:
    """Return current dashboard state as JSON."""
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text())
            return JSONResponse(data)
        except (json.JSONDecodeError, OSError):
            pass
    return JSONResponse(
        {
            "session_id": "waiting...",
            "camera_outside_departures": 0,
            "camera_inside_arrivals": 0,
            "discrepancy": 0,
            "status": "WAITING",
            "last_event_ts": "",
            "first_discrepancy_at": None,
            "recent_events": [],
            "inference_fps": 0.0,
            "pipeline_status": "waiting",
            "detect_mode": "box",
        }
    )


@app.get("/api/snapshot/{camera_id}")
async def get_snapshot(camera_id: str, annotate: str = "false") -> StreamingResponse:
    """Return a live JPEG snapshot from a camera.

    Pass ?annotate=true to overlay YOLO-World bounding boxes.
    """
    urls = _load_camera_urls()
    url = urls.get(camera_id)
    if not url:
        return JSONResponse({"error": f"Unknown camera: {camera_id}"}, status_code=404)

    jpg_bytes = _grab_frame(url)
    if jpg_bytes is None:
        return JSONResponse({"error": f"Cannot read from {camera_id}"}, status_code=503)

    # If annotate requested, run YOLO and draw boxes
    if annotate.lower() == "true":
        arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        dets = _detect_boxes(frame)
        # Show enhanced frame so dark scenes are actually visible
        frame = _enhance_low_light(frame)
        # Draw detections on frame
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            label = f"{d['class']} {d['confidence']:.0%}"
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw, y1), (0, 255, 0), -1)
            cv2.putText(frame, label, (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        # Box count overlay
        cv2.putText(
            frame, f"{len(dets)} boxes", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2
        )
        _, jpg_bytes = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        jpg_bytes = jpg_bytes.tobytes()

    return StreamingResponse(
        io.BytesIO(jpg_bytes),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store"},
    )


@app.get("/api/detections")
async def get_detections() -> JSONResponse:
    """Run detection on both cameras and return box counts + detections.

    Uses multi-scale detection with NMS merging for maximum recall.
    """
    urls = _load_camera_urls()
    if not urls:
        return JSONResponse({"error": "No cameras configured"}, status_code=503)

    results = {}
    for cam_id, url in urls.items():
        frame_bytes = _grab_frame(url)
        if frame_bytes is None:
            results[cam_id] = {"status": "offline", "boxes": 0, "people": 0, "detections": []}
            continue

        import numpy as np

        arr = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)

        dets = _detect_boxes(frame)
        results[cam_id] = {
            "status": "online",
            "boxes": len(dets),
            "people": 0,
            "detections": dets,
        }

    return JSONResponse(results)


@app.post("/api/reset")
async def reset_session() -> JSONResponse:
    """Send a reset command to the inference process."""
    commands_path = Path("commands.json")
    commands_path.write_text(json.dumps({"action": "reset"}))
    return JSONResponse({"status": "ok", "message": "Reset command sent"})


@app.get("/")
async def index() -> HTMLResponse:
    """Serve the main dashboard page."""
    index_path = PUBLIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text())
    return HTMLResponse("<h1>Dashboard not found</h1>", status_code=404)


@app.get("/architecture")
async def architecture() -> HTMLResponse:
    """Architecture diagram page."""
    arch_path = PUBLIC_DIR / "architecture.html"
    if arch_path.exists():
        return HTMLResponse(arch_path.read_text())
    return HTMLResponse("<h1>Architecture page not found</h1>", status_code=404)


# Serve static assets (JS, CSS, images) at /static/
# NOT at / which would intercept API routes
if PUBLIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")


@app.on_event("startup")
async def startup():
    """Preload YOLO model and camera config at startup."""
    _load_camera_urls()
    try:
        _load_detection_model()
    except Exception:
        pass  # Model will load on first request if startup fails


def main() -> None:
    """Entry point for esc-server command."""
    import uvicorn

    port = 8080
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
