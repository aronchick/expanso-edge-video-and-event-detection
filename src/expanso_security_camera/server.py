"""Self-hosted web dashboard server.

Runs on the Jetson alongside the Expanso pipeline. Serves the dashboard
UI, a JSON state API, and live camera snapshot endpoints.

Camera snapshots and detection counts are written to disk by the GPU
inference container (detect_loop.py) — this server just serves them.
No RTSP connections or YOLO inference needed here.

Run with:
    uv run esc-server

Then open http://<jetson-ip>:8080 in a browser.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

STATE_PATH = Path("state.json")
PUBLIC_DIR = Path(__file__).parent.parent.parent / "public"
SNAPSHOTS_DIR = Path("snapshots")

app = FastAPI(title="Box Transfer Monitor", docs_url=None, redoc_url=None)


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
    """Return the latest JPEG snapshot from a camera.

    Snapshots are written to disk by the GPU inference container with
    bounding boxes already drawn — no server-side YOLO needed.
    """
    snap_path = SNAPSHOTS_DIR / f"{camera_id}.jpg"
    if not snap_path.exists():
        return JSONResponse({"error": f"No snapshot for {camera_id}"}, status_code=404)

    try:
        jpg_bytes = snap_path.read_bytes()
    except OSError:
        return JSONResponse({"error": f"Cannot read snapshot for {camera_id}"}, status_code=503)

    return StreamingResponse(
        io.BytesIO(jpg_bytes),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store"},
    )


@app.get("/api/detections")
async def get_detections() -> JSONResponse:
    """Return box counts from the GPU inference container.

    Reads detections.json written by detect_loop.py — instant, no inference.
    """
    det_path = SNAPSHOTS_DIR / "detections.json"
    if not det_path.exists():
        return JSONResponse({"error": "No detection data available"}, status_code=503)

    try:
        data = json.loads(det_path.read_text())
        return JSONResponse(data)
    except (json.JSONDecodeError, OSError):
        return JSONResponse({"error": "Cannot read detection data"}, status_code=503)


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


def main() -> None:
    """Entry point for esc-server command."""
    import uvicorn

    port = 8080
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    # Ensure snapshots dir exists
    SNAPSHOTS_DIR.mkdir(exist_ok=True)

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
