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
import asyncio
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from expanso_security_camera.orchestrator.correlator import Correlator
from expanso_security_camera.orchestrator.jetson_wan import JetsonWanController
from expanso_security_camera.orchestrator.jobs_status import JobsStatus
from expanso_security_camera.orchestrator.metrics import Metrics
from expanso_security_camera.orchestrator.s3_watcher import S3Watcher
from expanso_security_camera.orchestrator.snapshots import (
    FakeSnapshotCache,
    read_real_snapshot,
    synthesize_awaiting_start,
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
    triggers = TriggerStore(triggers_path)
    # Correlator needs the trigger store so Rule 3 (drone-after-update)
    # can check whether "drone" is currently armed.
    correlator = Correlator(store, triggers=triggers)
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

        alert = correlator.evaluate(event)
        if alert:
            # Tag alert events as originating from the fusion-node so
            # downstream Bloblang filters and the dashboard's renderEvent
            # know to skip them as regular per-sector events. (The job
            # is still named "fusion-node" — that's the supervisor that
            # *computes* alerts, not the alert object itself.)
            store.insert({**alert, "node": "fusion-node"})
            metrics.record_fused()
            await manager.broadcast({"type": "alert", "data": alert})

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
        # JobsStatus.get() shells out to `expanso-cli job list` (sync subprocess
        # with a timeout). When the Jetson loses WAN, that subprocess blocks
        # for the full timeout waiting for a TCP connect to Expanso Cloud —
        # which would starve the FastAPI event loop and freeze the WS feed +
        # snapshot endpoints. Push it onto a worker thread so the loop keeps
        # spinning. (Bug seen during Beat 5A: dashboard appeared frozen.)
        return {"jobs": await asyncio.to_thread(jobs.get)}

    @app.get("/snapshot/{sector}")
    async def get_snapshot(sector: str) -> Response:
        # Real-mode first: serve the JPEG written by the sensor's detect_loop.
        # If a real frame is on disk, the sensor is producing — show it
        # regardless of what /jobs reports (real wins over abstract state).
        real = read_real_snapshot(snapshots_path, sector)
        if real is not None:
            return Response(
                content=real,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-cache, no-store"},
            )

        # No real frame. Decide whether to render the fake-feed (sensor is
        # supposed to be running, just no frame yet) or the awaiting-start
        # placeholder (sensor's Expanso job is stopped — Beat 0 lights-up).
        if sector.startswith("sensor-"):
            sensor_running = any(
                j.get("name") == sector and str(j.get("status", "")).lower() == "running"
                for j in jobs.get()
            )
            if not sensor_running:
                jpg = synthesize_awaiting_start(sector)
                return Response(
                    content=jpg,
                    media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store"},
                )

        # Fake fallback: synthesize so the dashboard isn't a sea of
        # broken-image icons during fake-mode rehearsals.
        if fake_mode:
            jpg = fake_cache.render(sector, cloud_up=metrics.is_cloud_up())
            return Response(
                content=jpg,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-cache, no-store"},
            )
        return JSONResponse({"error": f"no snapshot for {sector}"}, status_code=404)

    @app.get("/stream/{sector}")
    async def stream_snapshot(sector: str) -> StreamingResponse:
        """MJPEG stream — pushes each new snapshot file write to connected
        dashboards as a multipart/x-mixed-replace body. Browsers natively
        render this in an `<img>` tag as a continuous video. Latency tracks
        the sensor's snapshot write cadence (currently ~10fps) — far better
        than the 2s polling that /snapshot/{sector} alone supports."""
        import asyncio
        import os

        boundary = b"frameboundary"
        snap_file = snapshots_path / f"{sector}.jpg"

        async def gen():
            last_mtime = 0.0
            try:
                while True:
                    # Detect file change cheaply via mtime; read + yield only
                    # when a new frame has actually been written. Avoids re-
                    # streaming the same JPEG over and over (which would burn
                    # bandwidth without changing pixels).
                    try:
                        stat = os.stat(snap_file)
                        mtime = stat.st_mtime
                    except FileNotFoundError:
                        # No real frame yet — fall through to the synth path
                        # via a one-shot fake JPEG so the browser sees motion.
                        jpg = (
                            fake_cache.render(sector, cloud_up=metrics.is_cloud_up())
                            if fake_mode
                            else None
                        )
                        if jpg:
                            yield (
                                b"--" + boundary + b"\r\n"
                                b"Content-Type: image/jpeg\r\n"
                                b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                                + jpg + b"\r\n"
                            )
                        await asyncio.sleep(0.5)
                        continue

                    if mtime != last_mtime:
                        try:
                            jpg = snap_file.read_bytes()
                        except OSError:
                            await asyncio.sleep(0.05)
                            continue
                        last_mtime = mtime
                        yield (
                            b"--" + boundary + b"\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                            + jpg + b"\r\n"
                        )
                    # Tight sleep so we react quickly when the sensor writes
                    # a new frame; matches the sensor's ~100ms write cadence.
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                # Browser disconnected; let the generator unwind cleanly.
                return

        return StreamingResponse(
            gen(),
            media_type=f"multipart/x-mixed-replace; boundary={boundary.decode()}",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
        )

    # ── Auto-detect WAN state and broadcast cloud-up/down ──────────────
    # The big red banner only shows when `metrics.cloud_up` flips. F1 flips
    # it explicitly, but a manual `nmcli radio wifi off` (or any underlying
    # network failure) used to leave the dashboard ignorant. This loop
    # probes WAN reachability with a fast async TCP connect and broadcasts
    # cloud state on transitions, so the banner appears whether the operator
    # hits F1 or just yanks the cable.
    async def _wan_probe() -> bool:
        """Returns True if we can complete a TCP handshake to a known external."""
        try:
            # Cloudflare's 1.1.1.1:443 — globally available, very fast,
            # not an Expanso/AWS dependency (so probe failure means
            # general WAN loss, not "AWS happens to be slow").
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("1.1.1.1", 443), timeout=1.2
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except (asyncio.TimeoutError, OSError):
            return False

    async def wan_monitor_loop() -> None:
        last = metrics.is_cloud_up()
        # Small initial delay so the FastAPI lifespan can finish wiring up
        await asyncio.sleep(2.0)
        while True:
            try:
                up = await _wan_probe()
                if up != last:
                    metrics.set_cloud(up)
                    await manager.broadcast({"type": "cloud", "data": {"up": up}})
                    last = up
            except Exception:
                pass  # never let the monitor crash the app
            await asyncio.sleep(3.0)

    @app.on_event("startup")
    async def _start_wan_monitor() -> None:
        asyncio.create_task(wan_monitor_loop())

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

    @app.post("/demo/alert-test")
    async def alert_test() -> dict:
        """Fire a synthetic alert for rehearsal — bypasses the correlator
        rules so the operator can preview the dashboard takeover even
        when the cameras don't have the required scene set up.

        Old path /demo/fused-test still works (alias below) so anything
        in muscle memory keeps functioning.
        """
        synthetic = {
            "type": "alert",
            "rule": "synthetic",
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
        await manager.broadcast({"type": "alert", "data": synthetic})
        return {"ok": True}

    @app.post("/demo/fused-test")
    async def fused_test_alias() -> dict:
        """Legacy alias of /demo/alert-test — kept so STAGE_RUNBOOK.md's
        printed F3 curl still works after the alert rename."""
        return await alert_test()

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
        # Wrapper that always sends no-cache headers for the dashboard assets.
        # Stale CSS/JS during the live demo silently breaks behavior (the user
        # gets the new code only after a hard refresh) — eliminate that risk.
        from starlette.responses import FileResponse
        from starlette.staticfiles import StaticFiles as _SF

        class NoCacheStatic(_SF):
            def file_response(self, *args, **kwargs):  # type: ignore[override]
                resp = super().file_response(*args, **kwargs)
                if isinstance(resp, FileResponse):
                    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                    resp.headers["Pragma"] = "no-cache"
                    resp.headers["Expires"] = "0"
                return resp

        app.mount("/", NoCacheStatic(directory=str(PUBLIC_DIR), html=True), name="dashboard")

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
