"""Self-hosted web dashboard server.

Runs on the Jetson alongside the Expanso pipeline. Serves the dashboard
UI and a JSON API that the frontend polls for live state.

Run with:
    uv run esc-server

Then open http://<jetson-ip>:8080 in a browser.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

STATE_PATH = Path("state.json")
PUBLIC_DIR = Path(__file__).parent.parent.parent / "public"

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


# Serve static assets (JS, CSS, images)
if PUBLIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")


def main() -> None:
    """Entry point for esc-server command."""
    import uvicorn

    port = 8080
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    print(f"Dashboard server starting on http://0.0.0.0:{port}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
