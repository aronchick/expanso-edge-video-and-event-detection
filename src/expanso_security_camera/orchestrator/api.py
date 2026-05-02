"""FastAPI + WebSocket orchestrator.

Per HACKATHON_SCRIPT.md §9.3 + DEMO_UI_SPEC.md.

Endpoints:
  POST /events             — receive an event from a sensor
  GET  /triggers           — current trigger class list
  POST /triggers           — replace the trigger list (live class update beat)
  GET  /metrics            — totals + events/min for footer/header
  GET  /jobs               — Expanso job inventory for the platform tile
  GET  /snapshot/{sector}  — latest annotated camera frame (or synthesized in fake mode)
  POST /demo/wan-down      — stage shortcut: simulate cloud link DOWN
  POST /demo/wan-up        — stage shortcut: restore
  POST /demo/fused-test    — emit a synthetic fused alert (rehearsal only)
  WS   /ws                 — dashboard live feed

Run with:
    uv run edge-orchestrator             # binds 0.0.0.0:8080
    uv run edge-orchestrator --port 9000
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from expanso_security_camera.orchestrator.correlator import Correlator
from expanso_security_camera.orchestrator.jetson_wan import JetsonWanController
from expanso_security_camera.orchestrator.jobs_status import JobsStatus
from expanso_security_camera.orchestrator.metrics import Metrics
from expanso_security_camera.orchestrator.s3_watcher import S3Watcher
from expanso_security_camera.orchestrator.snapshots import (
    FakeSnapshotCache,
    read_real_snapshot,
)
from expanso_security_camera.orchestrator.store import EventStore
from expanso_security_camera.orchestrator.triggers import TriggerStore

PUBLIC_DIR = Path(__file__).parent.parent.parent.parent / "public" / "edge"


def create_app(
    db_path: str = "orchestrator.db",
    triggers_path: str | Path = "triggers.yaml",
    snapshots_dir: str | Path = "snapshots",
    ndjson_path: str | Path | None = "events.ndjson",
    fake_mode: bool = True,
    s3_bucket: str | None = None,
    jetson_host: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Edge ISR Orchestrator", docs_url=None, redoc_url=None)
    store = EventStore(db_path, ndjson_path=ndjson_path)
    correlator = Correlator(store)
    triggers = TriggerStore(triggers_path)
    metrics = Metrics()
    jobs = JobsStatus()
    snapshots_path = Path(snapshots_dir)
    fake_cache = FakeSnapshotCache()
    s3 = S3Watcher(bucket=s3_bucket)
    s3.start()
    wan = JetsonWanController(jetson_host=jetson_host)

    class ConnectionManager:
        def __init__(self) -> None:
            self.active: list[WebSocket] = []

        async def connect(self, ws: WebSocket) -> None:
            await ws.accept()
            self.active.append(ws)

        def disconnect(self, ws: WebSocket) -> None:
            if ws in self.active:
                self.active.remove(ws)

        async def broadcast(self, message: dict) -> None:
            dead = []
            for ws in self.active:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.disconnect(ws)

    manager = ConnectionManager()

    @app.post("/events")
    async def receive_event(request: Request) -> dict:
        event = await request.json()

        # If the operator simulated cloud-down, strip Gemini descriptions so
        # the dashboard reflects degraded state for events received during
        # the simulated outage. The sensor itself doesn't know we're
        # pretending; this is a stage convenience.
        if not metrics.is_cloud_up() and event.get("gemini_description"):
            event["gemini_description"] = None
            if isinstance(event.get("model_versions"), dict):
                event["model_versions"]["gemini"] = None

        store.insert(event)
        metrics.record_event(
            signed=bool(event.get("signature")),
            queued_offline=bool(event.get("queued_offline")),
        )
        fake_cache.update(event)
        await manager.broadcast({"type": "event", "data": event})

        fused = correlator.evaluate(event)
        if fused:
            # Tag fused events as originating from the fusion-node so
            # downstream Bloblang filters and the dashboard's renderEvent
            # know to skip them as regular per-sector events. (Renamed from
            # "orchestrator" — see jobs_status.py for naming rationale.)
            store.insert({**fused, "node": "fusion-node"})
            metrics.record_fused()
            await manager.broadcast({"type": "fused", "data": fused})

        return {"status": "ok"}

    @app.get("/triggers")
    async def get_triggers() -> dict:
        return {"triggers": triggers.get()}

    @app.post("/triggers")
    async def set_triggers(request: Request) -> dict:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "body must be a JSON object with a 'triggers' list"},
                status_code=400,
            )
        new_list = body.get("triggers", [])
        if not isinstance(new_list, list):
            return JSONResponse({"error": "triggers must be a list"}, status_code=400)
        applied = triggers.set(new_list)
        await manager.broadcast({"type": "triggers", "data": applied})
        return {"triggers": applied}

    @app.get("/metrics")
    async def get_metrics() -> dict:
        return metrics.snapshot()

    @app.get("/jobs")
    async def get_jobs() -> dict:
        return {"jobs": jobs.get()}

    @app.get("/snapshot/{sector}")
    async def get_snapshot(sector: str) -> Response:
        # Real-mode first: serve the JPEG written by the sensor's detect_loop.
        real = read_real_snapshot(snapshots_path, sector)
        if real is not None:
            return Response(
                content=real,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-cache, no-store"},
            )
        # Fake fallback: synthesize so the dashboard isn't a sea of broken-image icons.
        if fake_mode:
            jpg = fake_cache.render(sector, cloud_up=metrics.is_cloud_up())
            return Response(
                content=jpg,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-cache, no-store"},
            )
        return JSONResponse({"error": f"no snapshot for {sector}"}, status_code=404)

    @app.post("/demo/wan-down")
    async def wan_down() -> dict:
        # Always flip the in-memory flag first so the dashboard responds
        # immediately, even if the SSH call later fails. The cosmetic flag
        # drives event-stripping; the SSH call drives the real radio toggle.
        metrics.set_cloud(False)
        await manager.broadcast({"type": "cloud", "data": {"up": False}})
        result = await wan.set_wan(up=False)
        await manager.broadcast(
            {
                "type": "cloud",
                "data": {
                    "up": False,
                    "real_toggle_ok": result.ok and not result.cosmetic_only,
                    "detail": result.detail,
                },
            }
        )
        return {"cloud_up": False, "real_toggle_ok": result.ok, "detail": result.detail}

    @app.post("/demo/wan-up")
    async def wan_up() -> dict:
        metrics.set_cloud(True)
        await manager.broadcast({"type": "cloud", "data": {"up": True}})
        result = await wan.set_wan(up=True)
        await manager.broadcast(
            {
                "type": "cloud",
                "data": {
                    "up": True,
                    "real_toggle_ok": result.ok and not result.cosmetic_only,
                    "detail": result.detail,
                },
            }
        )
        return {"cloud_up": True, "real_toggle_ok": result.ok, "detail": result.detail}

    @app.get("/s3")
    async def s3_state() -> dict:
        return s3.snapshot()

    @app.get("/s3/object")
    async def s3_object(key: str) -> Response:
        # Key passed as a query param (?key=events/dt=.../...) so leading
        # slashes and equals signs in the partition key don't collide with
        # FastAPI's path-parameter parser.
        result = s3.fetch_object(key)
        if result is None:
            return JSONResponse({"error": "S3 not configured"}, status_code=503)
        return JSONResponse(result)

    @app.post("/demo/fused-test")
    async def fused_test() -> dict:
        synthetic = {
            "type": "multi_sector_correlation",
            "ts": time.time(),
            "sectors": ["sensor-north", "sensor-south"],
            "contacts": [
                {
                    "sector": "sensor-north",
                    "yolo_hits": ["person", "backpack"],
                    "description": "Adult with shoulder pack.",
                },
                {
                    "sector": "sensor-south",
                    "yolo_hits": ["drone"],
                    "description": "Small quadcopter, civilian pattern.",
                },
            ],
        }
        metrics.record_fused()
        await manager.broadcast({"type": "fused", "data": synthetic})
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await manager.connect(ws)
        # On connect, send everything the dashboard needs to render its initial state.
        backfill = store.recent(since_ts=time.time() - 30, limit=100)
        await ws.send_json({"type": "backfill", "data": backfill})
        await ws.send_json({"type": "triggers", "data": triggers.get()})
        await ws.send_json({"type": "cloud", "data": {"up": metrics.is_cloud_up()}})
        await ws.send_json({"type": "jobs", "data": jobs.get()})
        await ws.send_json({"type": "metrics", "data": metrics.snapshot()})
        await ws.send_json({"type": "s3", "data": s3.snapshot()})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(ws)

    if PUBLIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="dashboard")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Edge-ISR orchestrator")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", default="orchestrator.db")
    parser.add_argument("--triggers", default="triggers.yaml")
    parser.add_argument(
        "--snapshots", default="snapshots", help="dir where sensors write annotated JPEGs"
    )
    parser.add_argument(
        "--ndjson",
        default="events.ndjson",
        help="event log NDJSON tail file for the archive pipeline",
    )
    parser.add_argument(
        "--no-fake-snapshots",
        action="store_true",
        help="disable synthesized snapshots when no real frames are present",
    )
    parser.add_argument(
        "--s3-bucket",
        default=None,
        help="S3 archive bucket; falls back to ARMYX_S3_BUCKET env",
    )
    parser.add_argument(
        "--jetson-host",
        default=None,
        help="ssh user@host for the Jetson; falls back to ARMYX_JETSON_HOST env",
    )
    args = parser.parse_args()

    app = create_app(
        db_path=args.db,
        triggers_path=args.triggers,
        snapshots_dir=args.snapshots,
        ndjson_path=args.ndjson,
        fake_mode=not args.no_fake_snapshots,
        s3_bucket=args.s3_bucket,
        jetson_host=args.jetson_host,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
