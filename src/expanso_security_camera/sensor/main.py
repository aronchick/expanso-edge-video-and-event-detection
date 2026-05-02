"""Sensor entrypoint. One process per camera, one node identity.

Per HACKATHON_SCRIPT.md §8.5. Configured via env vars so the same image
runs as either sensor-north or sensor-south just by changing the
Expanso job spec.

Three modes:

  edge-sensor                          # real: RTSP + YOLO + Gemini (needs Jetson)
  edge-sensor --fake                   # synthetic events from one node
  edge-sensor --fake --multi           # synthetic events from BOTH sectors
                                       # (good for laptop end-to-end testing)

Required env vars in real mode:
  RTSP_URL              rtsp://...
  NODE_ID               sensor-north | sensor-south
  ORCHESTRATOR_URL      http://192.168.50.30:8080
  GOOGLE_API_KEY        Gemini API key (also accepts GEMINI_API_KEY)

Optional:
  YOLO_MODEL            path to .engine or .pt (default: yolo11s.engine)
  DB_PATH               sqlite db path (default: /data/<node>.db)
"""

from __future__ import annotations

import argparse
import os
import random
import threading
import time

from expanso_security_camera.sensor.dbom import sign_event
from expanso_security_camera.sensor.emitter import Emitter
from expanso_security_camera.sensor.schema import Detection, Event
from expanso_security_camera.sensor.triggers_client import TriggerClient

# ── Real sensor loop ────────────────────────────────────────────────────


def run_real(
    node_id: str,
    rtsp_url: str,
    orchestrator_url: str,
    db_path: str,
    yolo_model: str,
    snapshot_dir: str = "snapshots",
) -> None:
    # Defer heavy imports so --fake doesn't pay for them.
    from pathlib import Path

    import cv2

    from expanso_security_camera.sensor.detector import Detector
    from expanso_security_camera.sensor.pipeline import FreshFrameReader

    print(f"[{node_id}] starting real sensor, RTSP={rtsp_url}", flush=True)
    triggers = TriggerClient(orchestrator_url)
    reader = FreshFrameReader(rtsp_url, name=node_id)
    detector = Detector(node_id, triggers, model_path=yolo_model)
    emitter = Emitter(node_id, db_path, orchestrator_url)

    snapshot_path = Path(snapshot_dir) / f"{node_id}.jpg"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_INTERVAL_SEC = 0.4  # noqa: N806 — function-local constant
    last_snapshot_ts = 0.0
    last_event_for_overlay: object = None
    LAST_EVENT_HOLD_SEC = 1.5  # noqa: N806 — function-local constant

    print(f"[{node_id}] warmed up, entering main loop", flush=True)
    while True:
        result = reader.read()
        if result is None:
            time.sleep(0.05)
            continue
        frame, ts = result
        event = detector.detect(frame, ts)

        # Throttled snapshot write — gives the dashboard a real, live camera feed
        # with annotations. Writes even on no-detection frames so the feed never
        # freezes; overlays the last detection's boxes for a brief hold so the
        # demo doesn't strobe.
        if event is not None:
            last_event_for_overlay = (event, ts)
        if ts - last_snapshot_ts >= SNAPSHOT_INTERVAL_SEC:
            overlay_event = None
            if last_event_for_overlay is not None:
                cached_event, cached_ts = last_event_for_overlay
                if ts - cached_ts <= LAST_EVENT_HOLD_SEC:
                    overlay_event = cached_event
            annotated = detector.annotate(frame, overlay_event)
            # Atomic write so the orchestrator never reads a half-encoded JPEG.
            tmp_path = snapshot_path.with_suffix(".jpg.tmp")
            ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if ok:
                tmp_path.write_bytes(buf.tobytes())
                tmp_path.replace(snapshot_path)
            last_snapshot_ts = ts

        if event is None:
            time.sleep(0.05)
            continue
        sign_event(event)
        emitter.emit(event)
        labels = [h.label for h in event.yolo_hits]
        print(f"[{node_id}] emitted: {labels}", flush=True)


# ── Fake sensor loop (no GPU, no cameras) ───────────────────────────────


_FAKE_SCENES: list[dict] = [
    {"hits": [("person", 0.78)], "weight": 4},
    {"hits": [("person", 0.83), ("backpack", 0.71)], "weight": 3},
    {"hits": [("airplane", 0.66)], "weight": 2},  # YOLO-classifies drone as airplane
    {"hits": [("car", 0.81)], "weight": 2},
    {"hits": [("truck", 0.74)], "weight": 1},
    {"hits": [("person", 0.69), ("cell phone", 0.62)], "weight": 1},
    {"hits": [], "weight": 5},  # no detection — most frames are quiet
]
_FAKE_DESCRIPTIONS = {
    ("person",): "Single adult ambulating through frame, no carried items visible.",
    ("backpack", "person"): "Adult with shoulder pack, posture suggests moderate load.",
    ("airplane",): "Small quadcopter form, low altitude, civilian pattern.",
    ("car",): "Passenger sedan, civilian color scheme, no markings.",
    ("truck",): "Light transport class vehicle, civilian.",
    ("cell phone", "person"): "Adult holding handheld device at eye level.",
}


def _fake_one_event(node_id: str, simulate_offline: bool) -> Event | None:
    scenes = []
    for s in _FAKE_SCENES:
        scenes.extend([s] * s["weight"])
    scene = random.choice(scenes)
    if not scene["hits"]:
        return None
    hits = [
        Detection(label=label, confidence=conf, bbox=(0.0, 0.0, 100.0, 100.0))
        for label, conf in scene["hits"]
    ]
    labels_key = tuple(sorted({h.label for h in hits}))
    desc = None if simulate_offline else _FAKE_DESCRIPTIONS.get(labels_key)
    return Event(
        node=node_id,
        ts=time.time(),
        yolo_hits=hits,
        gemini_description=desc,
        model_versions={
            "yolo": "fake-yolo11s",
            "gemini": "fake-gemini-3-flash" if desc else None,
        },
    )


def run_fake_node(
    node_id: str,
    orchestrator_url: str,
    db_path: str,
    triggers: TriggerClient,
    cadence_sec: float = 2.5,
    offline_after_sec: float | None = None,
) -> None:
    """Generate synthetic events for one node identity.

    If `offline_after_sec` is set, after that many seconds Gemini
    descriptions stop appearing — simulating a WAN-yank.
    """
    emitter = Emitter(node_id, db_path, orchestrator_url)
    started = time.time()
    print(f"[{node_id}] fake sensor running, cadence={cadence_sec}s", flush=True)
    while True:
        elapsed = time.time() - started
        offline = offline_after_sec is not None and elapsed > offline_after_sec
        event = _fake_one_event(node_id, simulate_offline=offline)
        if event is not None:
            # Trigger filter still applies — gives the live-update demo something to gate.
            event.yolo_hits = [h for h in event.yolo_hits if triggers.contains(h.label)]
            if event.yolo_hits:
                sign_event(event)
                emitter.emit(event)
                labels = [h.label for h in event.yolo_hits]
                marker = " [offline]" if offline else ""
                print(f"[{node_id}] emitted: {labels}{marker}", flush=True)
        # Add some jitter so the two nodes drift in/out of correlator window naturally.
        time.sleep(cadence_sec * (0.7 + random.random() * 0.6))


# ── Entrypoint ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Edge-ISR sensor")
    parser.add_argument(
        "--fake", action="store_true", help="generate synthetic events (no GPU, no cameras)"
    )
    parser.add_argument(
        "--multi",
        action="store_true",
        help="(fake mode) run sensor-north + sensor-south in one process",
    )
    parser.add_argument(
        "--orchestrator", default=os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")
    )
    parser.add_argument("--node-id", default=os.environ.get("NODE_ID", "sensor-fake"))
    parser.add_argument("--rtsp-url", default=os.environ.get("RTSP_URL"))
    parser.add_argument("--yolo-model", default=os.environ.get("YOLO_MODEL", "yolo11s.engine"))
    parser.add_argument("--db", default=None)
    parser.add_argument(
        "--cadence", type=float, default=2.5, help="(fake) seconds between event attempts"
    )
    parser.add_argument(
        "--offline-after",
        type=float,
        default=None,
        help="(fake) drop Gemini descriptions after N seconds",
    )
    args = parser.parse_args()

    if args.fake:
        triggers = TriggerClient(args.orchestrator)
        if args.multi:
            threads = []
            for node in ("sensor-north", "sensor-south"):
                db = args.db or f"{node}.db"
                t = threading.Thread(
                    target=run_fake_node,
                    args=(node, args.orchestrator, db, triggers, args.cadence, args.offline_after),
                    daemon=True,
                    name=node,
                )
                t.start()
                threads.append(t)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("shutting down", flush=True)
        else:
            db = args.db or f"{args.node_id}.db"
            run_fake_node(
                args.node_id,
                args.orchestrator,
                db,
                triggers,
                cadence_sec=args.cadence,
                offline_after_sec=args.offline_after,
            )
        return

    if not args.rtsp_url:
        raise SystemExit("real mode requires RTSP_URL or --rtsp-url")
    db = args.db or f"/data/{args.node_id}.db"
    run_real(args.node_id, args.rtsp_url, args.orchestrator, db, args.yolo_model)


if __name__ == "__main__":
    main()
