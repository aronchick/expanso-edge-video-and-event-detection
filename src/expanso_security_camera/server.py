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

    Prefers a fine-tuned model if available, falls back to YOLO-World.
    """
    global _detection_model
    if _detection_model is None:
        from ultralytics import YOLO

        os.environ["YOLO_VERBOSE"] = "false"

        finetuned = Path("box-detector-finetuned.pt")
        if finetuned.exists():
            _detection_model = YOLO(str(finetuned))
        else:
            _detection_model = YOLO("yolov8s-worldv2.pt")
            _detection_model.set_classes(BOX_CLASSES)
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


def _enhance_frame(frame):
    """Light CLAHE enhancement for low-light frames. Fast (<5ms)."""
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    return cv2.cvtColor(cv2.merge([l_channel, a_channel, b_channel]), cv2.COLOR_LAB2BGR)


def _grab_frame(url: str) -> bytes | None:
    """Grab a single fresh frame from an RTSP stream, return as JPEG bytes."""
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        return None
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

    Pass ?annotate=true to overlay bounding boxes.
    """
    urls = _load_camera_urls()
    url = urls.get(camera_id)
    if not url:
        return JSONResponse({"error": f"Unknown camera: {camera_id}"}, status_code=404)

    jpg_bytes = _grab_frame(url)
    if jpg_bytes is None:
        return JSONResponse({"error": f"Cannot read from {camera_id}"}, status_code=503)

    if annotate.lower() == "true":
        arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        model = _load_detection_model()
        # Enhance + detect
        enhanced = _enhance_frame(frame)
        results = model(enhanced, verbose=False, conf=0.10)
        frame = results[0].plot()
        _, jpg_bytes = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        jpg_bytes = jpg_bytes.tobytes()

    return StreamingResponse(
        io.BytesIO(jpg_bytes),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store"},
    )


@app.get("/api/detections")
async def get_detections() -> JSONResponse:
    """Run YOLO on both cameras and return box counts + detections."""
    urls = _load_camera_urls()
    if not urls:
        return JSONResponse({"error": "No cameras configured"}, status_code=503)

    try:
        model = _load_detection_model()
    except Exception as e:
        return JSONResponse({"error": f"Model load failed: {e}"}, status_code=503)

    results = {}
    for cam_id, url in urls.items():
        frame_bytes = _grab_frame(url)
        if frame_bytes is None:
            results[cam_id] = {"status": "offline", "boxes": 0, "people": 0, "detections": []}
            continue

        arr = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        enhanced = _enhance_frame(frame)

        preds = model(enhanced, verbose=False, conf=0.10)
        dets = []
        boxes = 0
        if preds and preds[0].boxes is not None:
            for box in preds[0].boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                name = model.names[cls_id]
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                boxes += 1
                dets.append({"class": name, "confidence": round(conf, 2), "bbox": [x1, y1, x2, y2]})

        results[cam_id] = {"status": "online", "boxes": boxes, "people": 0, "detections": dets}

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


if PUBLIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")


@app.on_event("startup")
async def startup():
    """Preload YOLO model and camera config at startup."""
    _load_camera_urls()
    try:
        _load_detection_model()
    except Exception:
        pass


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
