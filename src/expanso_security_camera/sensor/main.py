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

from dotenv import load_dotenv

from expanso_security_camera.sensor.dbom import sign_event
from expanso_security_camera.sensor.emitter import Emitter
from expanso_security_camera.sensor.schema import Detection, Event
from expanso_security_camera.sensor.triggers_client import TriggerClient

# ── Real sensor loop ────────────────────────────────────────────────────


def run_real(
    node_id: str,
    rtsp_url: str | int,
    orchestrator_url: str,
    db_path: str,
    yolo_model: str,
    drone_yolo_model: str | None = None,
    snapshot_dir: str = "snapshots",
) -> None:
    # Defer heavy imports so --fake doesn't pay for them.
    from pathlib import Path

    import cv2

    from expanso_security_camera.sensor.detector import Detector
    from expanso_security_camera.sensor.pipeline import FreshFrameReader

    src_kind = "webcam" if str(rtsp_url).isdigit() else "RTSP"
    print(f"[{node_id}] starting real sensor, {src_kind}={rtsp_url}", flush=True)
    triggers = TriggerClient(orchestrator_url)
    reader = FreshFrameReader(rtsp_url, name=node_id)
    detector = Detector(
        node_id,
        triggers,
        model_path=yolo_model,
        drone_model_path=drone_yolo_model,
    )
    emitter = Emitter(node_id, db_path, orchestrator_url)

    snapshot_path = Path(snapshot_dir) / f"{node_id}.jpg"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    # ≈10 FPS — orchestrator's /stream/{sector} MJPEG endpoint pushes each new
    # snapshot file write to connected dashboards, so visible cadence matches.
    SNAPSHOT_INTERVAL_SEC = 0.1  # noqa: N806 — function-local constant
    last_snapshot_ts = 0.0
    LAST_EVENT_HOLD_SEC = 1.5  # noqa: N806 — function-local constant

    # YOLO runs on a background thread so it doesn't block the snapshot/write
    # loop. The main loop reads RTSP frames at full rate (~10fps) and writes
    # annotated snapshots using whatever the most recent detection is. YOLO
    # picks up the LATEST frame from a single-slot mailbox, runs inference
    # (slow on .pt), publishes the result, and grabs the next-latest frame.
    # Net effect: video stays smooth even when YOLO is taking ~500ms+ per call.
    import threading

    yolo_inbox: dict = {"frame": None, "ts": 0.0}
    yolo_out: dict = {"event": None, "ts": 0.0}
    yolo_lock = threading.Lock()
    yolo_event = threading.Event()

    # Cap inference rate. This is DECOUPLED from the WS emit rate (see
    # EMIT_INTERVAL_SEC below) — inference only feeds the 10 fps annotated
    # snapshot stream, so a higher cap just means the bounding boxes track the
    # video at its own frame rate instead of stepping. 10 fps per sensor matches
    # the snapshot cadence (SNAPSHOT_INTERVAL_SEC = 0.1) for near-real-time box
    # tracking; going higher than the snapshot rate just wastes inference.
    # Configurable via EDGE_INFERENCE_FPS_CAP.
    inference_min_gap_sec = 1.0 / float(os.environ.get("EDGE_INFERENCE_FPS_CAP", "10.0"))
    last_inference_ts = 0.0

    def yolo_worker() -> None:
        """Run YOLO inference at the configured FPS cap and store the
        latest result. Emit cadence is owned by the main loop now (2s
        heartbeat below) — this thread only updates `yolo_out` so the
        main loop can read it whenever the heartbeat tick lands.
        """
        nonlocal last_inference_ts
        while True:
            yolo_event.wait()
            yolo_event.clear()
            # Throttle: skip this wakeup if we ran inference too recently.
            # The mailbox keeps the latest frame, so the next wakeup will
            # still see fresh content — we just drop intermediate frames.
            now = time.time()
            if now - last_inference_ts < inference_min_gap_sec:
                continue
            with yolo_lock:
                frame = yolo_inbox["frame"]
                ts = yolo_inbox["ts"]
                yolo_inbox["frame"] = None
            if frame is None:
                continue
            last_inference_ts = now
            try:
                ev = detector.detect(frame, ts)
            except Exception as e:
                print(f"[{node_id}] yolo error: {e}", flush=True)
                continue
            with yolo_lock:
                yolo_out["event"] = ev
                yolo_out["ts"] = ts

    threading.Thread(target=yolo_worker, daemon=True, name=f"{node_id}-yolo").start()

    # Heartbeat emit cadence: produce one event every EMIT_INTERVAL_SEC
    # whether or not the latest YOLO inference found anything. This drives the
    # dashboard's zone counts + CROWD FLAG alert, so it IS the perceived
    # detection latency. 0.5s (~2 events/sec/sensor, ~4/sec total) keeps the
    # numbers feeling live without firehosing the WebSocket. If the latest
    # detection is recent (within the cadence window), emit it with hits;
    # otherwise emit an empty pulse so the dashboard shows "still here, no
    # contact" instead of going silent. Override with EDGE_EMIT_INTERVAL_SEC.
    EMIT_INTERVAL_SEC = float(os.environ.get("EDGE_EMIT_INTERVAL_SEC", "0.5"))  # noqa: N806
    last_emit_ts = 0.0

    print(
        f"[{node_id}] warmed up, entering main loop (emit cadence {EMIT_INTERVAL_SEC}s)", flush=True
    )
    while True:
        result = reader.read()
        if result is None:
            time.sleep(0.02)
            continue
        frame, ts = result

        # Hand the latest frame to the YOLO worker (single-slot mailbox —
        # newer frames overwrite older unprocessed ones, so YOLO always
        # works on the most recent frame even if it's slow).
        with yolo_lock:
            yolo_inbox["frame"] = frame
            yolo_inbox["ts"] = ts
            latest_event = yolo_out["event"]
            latest_event_ts = yolo_out["ts"]
        yolo_event.set()

        # Snapshot write at full RTSP rate, decoupled from YOLO inference.
        if ts - last_snapshot_ts >= SNAPSHOT_INTERVAL_SEC:
            overlay_event = None
            if latest_event is not None and (ts - latest_event_ts) <= LAST_EVENT_HOLD_SEC:
                overlay_event = latest_event
            annotated = detector.annotate(frame, overlay_event)
            tmp_path = snapshot_path.with_suffix(".jpg.tmp")
            ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if ok:
                tmp_path.write_bytes(buf.tobytes())
                tmp_path.replace(snapshot_path)
            last_snapshot_ts = ts

        # Heartbeat emit. Drives the dashboard's event panel cadence.
        now = time.time()
        if now - last_emit_ts >= EMIT_INTERVAL_SEC:
            if latest_event is not None and (now - latest_event_ts) <= EMIT_INTERVAL_SEC:
                # Recent detection → emit it. Detector already populated
                # gemini_description (real or canned) when it last ran.
                ev = latest_event
            else:
                # No fresh detection → emit an "empty" pulse so the
                # dashboard renders a light-gray "empty" row instead of
                # going silent. yolo_hits=[] is the marker.
                from expanso_security_camera.sensor.schema import Event

                ev = Event(
                    node=node_id,
                    ts=now,
                    yolo_hits=[],
                    gemini_description=None,
                    model_versions={"yolo": detector.model_name, "gemini": None},
                )
            sign_event(ev)
            emitter.emit(ev)
            labels = [h.label for h in ev.yolo_hits] or ["empty"]
            print(f"[{node_id}] emitted: {labels}", flush=True)
            last_emit_ts = now

        # Tiny sleep so we don't pin a CPU core when RTSP is firing fast.
        time.sleep(0.02)


# ── Fake sensor loop (no GPU, no cameras) ───────────────────────────────


# People-counting demo. Occupancy per zone is a smooth RANDOM WALK (±1 per
# tick, biased toward staying) rather than an i.i.d. random crowd size, so
# the counts drift like real foot traffic — people arrive and leave one at a
# time — instead of strobing between unrelated numbers every tick. The two
# zones walk independently, so the COMBINED total still wanders above and
# below the threshold on its own and trips the crowd FLAG hands-off.
_FAKE_MAX_OCCUPANCY = 6
# Step distribution: mostly hold, sometimes ±1 (gentle drift).
_FAKE_STEPS = (-1, 0, 0, 0, +1)
# Per-node current occupancy, persisted across ticks (module-level state).
_fake_occupancy: dict[str, int] = {}

# Fine-tune: occasionally a person is carrying a backpack. Only surfaces if
# the operator has armed the backpack trigger (filter in run_fake_node).
_FAKE_BACKPACK_PROB = 0.25


def _fake_step_occupancy(node_id: str) -> int:
    """Advance this zone's occupancy by one smooth random-walk step."""
    cur = _fake_occupancy.get(node_id)
    if cur is None:
        cur = random.randint(1, 3)  # seed somewhere reasonable
    cur = max(0, min(_FAKE_MAX_OCCUPANCY, cur + random.choice(_FAKE_STEPS)))
    _fake_occupancy[node_id] = cur
    return cur


def _spread_bbox(i: int, n: int) -> tuple[float, float, float, float]:
    """Lay out up to `n` person boxes across the frame so the overlay shows
    distinct track gates instead of a single stacked box. Source space is
    the 1280x720 synthesized snapshot the dashboard renders in fake mode."""
    w, h = 1280.0, 720.0
    box_w, box_h = 150.0, 320.0
    if n <= 1:
        cx = w * 0.5
    else:
        # Evenly distribute centers across the middle 80% of the width.
        cx = w * (0.1 + 0.8 * (i / (n - 1)))
    # Slight vertical jitter (deterministic by index) so they don't form a
    # perfect row — reads more like real foot traffic.
    cy = h * (0.52 + 0.06 * ((i % 2) * 2 - 1))
    return (cx - box_w / 2, cy - box_h / 2, cx + box_w / 2, cy + box_h / 2)


def _fake_one_event(node_id: str, simulate_offline: bool) -> Event:
    """One synthetic event reflecting this zone's current occupancy. Always
    returns an Event (possibly with zero hits) so the dashboard count settles
    to 0 promptly when the zone empties, instead of waiting for staleness."""
    n = _fake_step_occupancy(node_id)

    hits: list[Detection] = []
    for i in range(n):
        conf = round(random.uniform(0.62, 0.93), 2)
        hits.append(Detection(label="person", confidence=conf, bbox=_spread_bbox(i, n)))
    # One of the people might be carrying a backpack (high-value object).
    if n and random.random() < _FAKE_BACKPACK_PROB:
        bx1, by1, bx2, by2 = hits[0].bbox
        hits.append(
            Detection(
                label="backpack",
                confidence=round(random.uniform(0.55, 0.8), 2),
                bbox=(bx1 + 20, by1 + 90, bx1 + 110, by1 + 200),
            )
        )

    desc = None
    if n and not simulate_offline:
        carried = any(h.label == "backpack" for h in hits)
        desc = (
            f"{n} adult(s) in frame"
            + (", one carrying a shoulder pack" if carried else "")
            + ", civilian foot traffic."
        )
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
        # Trigger filter still applies — gives the live-update demo something
        # to gate. We emit EVERY tick (even with zero hits) so the zone count
        # settles to 0 promptly when the zone empties.
        event.yolo_hits = [h for h in event.yolo_hits if triggers.contains(h.label)]
        sign_event(event)
        emitter.emit(event)
        labels = [h.label for h in event.yolo_hits] or ["empty"]
        marker = " [offline]" if offline else ""
        print(f"[{node_id}] emitted: {labels}{marker}", flush=True)
        # Mild jitter so the two zones don't update in lockstep.
        time.sleep(cadence_sec * (0.85 + random.random() * 0.3))


# ── Entrypoint ──────────────────────────────────────────────────────────


def main() -> None:
    load_dotenv()  # see orchestrator/api.py main() for why
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
    parser.add_argument(
        "--cameras",
        default=os.environ.get("EDGE_CAMERAS"),
        help="Booth mode: comma-separated USB webcam indices (or RTSP URLs), "
        "one per zone, run in ONE process. e.g. --cameras 0,1 maps index 0 → "
        "sensor-north and index 1 → sensor-south. Run `esc-test-cameras "
        "--list` first to find your indices.",
    )
    parser.add_argument("--yolo-model", default=os.environ.get("YOLO_MODEL", "yolo11s.engine"))
    parser.add_argument(
        "--drone-yolo-model",
        default=os.environ.get("DRONE_YOLO_MODEL"),
        help="Optional secondary engine consulted only for the drone class. "
        "Pair with a COCO yolov8s.engine primary so person/backpack come from "
        "the dense COCO model and drone comes from the fine-tune.",
    )
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

    # ── Booth mode: two USB webcams, one process ─────────────────────
    # `--cameras 0,1` runs sensor-north + sensor-south as threads in this
    # single process — the mirror of `--fake --multi`, but with real
    # capture. This is the one-command path for the conference booth Mac.
    if args.cameras:
        sources = [s.strip() for s in str(args.cameras).split(",") if s.strip()]
        if not sources:
            raise SystemExit("--cameras given but no indices/URLs parsed")
        zone_names = ("sensor-north", "sensor-south", "sensor-east", "sensor-west")
        threads = []
        for idx, src in enumerate(sources):
            node = zone_names[idx] if idx < len(zone_names) else f"sensor-{idx}"
            db = args.db or f"{node}.db"
            # device index stays an int so pipeline picks the webcam path;
            # anything non-numeric (rtsp://...) passes through unchanged.
            source: str | int = int(src) if src.isdigit() else src
            t = threading.Thread(
                target=run_real,
                args=(node, source, args.orchestrator, db, args.yolo_model),
                kwargs={"drone_yolo_model": args.drone_yolo_model},
                daemon=True,
                name=node,
            )
            t.start()
            threads.append(t)
            print(f"[{node}] booth camera thread started on source={src!r}", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("shutting down", flush=True)
        return

    if not args.rtsp_url:
        raise SystemExit("real mode requires RTSP_URL, --rtsp-url, or --cameras")
    db = args.db or f"/data/{args.node_id}.db"
    run_real(
        args.node_id,
        args.rtsp_url,
        args.orchestrator,
        db,
        args.yolo_model,
        drone_yolo_model=args.drone_yolo_model,
    )


if __name__ == "__main__":
    main()
