"""Replay a recorded scene through the sensor's real decision path.

A scene is JSON Lines. An optional first line carries the provenance:

    {"scene": {"model": "drone-3class-v2", "source": "...", "frames": 40}}

Every other line is one captured frame and what YOLO measured on it, before
any trigger or threshold decision:

    {"offset_s": 0.5, "frame": {"index": 7, "width": 640, "height": 480},
     "detections": [{"label": "person", "confidence": 0.81, "bbox": [x1, y1, x2, y2]}]}

Replay applies the live trigger list and the per-class floors, asks the
analyst, signs the event, queues it in SQLite and posts it to the
orchestrator exactly like live capture. Only the pixels and the YOLO forward
pass are replaced by their recorded result. `scripts/record-scene.py` makes a
scene from images or video with the real weights.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from expanso_security_camera.sensor.cascade import Analyst, build_event, log, select_hits
from expanso_security_camera.sensor.dbom import sign_event
from expanso_security_camera.sensor.emitter import Emitter
from expanso_security_camera.sensor.schema import Event
from expanso_security_camera.sensor.triggers_client import TriggerClient


def load_scene(path: str | Path) -> tuple[dict, list[dict]]:
    """Return (provenance, frames). Raises ValueError on a malformed scene."""
    header: dict = {}
    frames: list[dict] = []

    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue

        record = json.loads(line)

        if "scene" in record:
            header = record["scene"]
            continue

        if "offset_s" not in record or "detections" not in record:
            raise ValueError(f"{path}:{number}: a frame needs offset_s and detections")

        frames.append(record)

    if not frames:
        raise ValueError(f"{path}: scene has no frames")

    return header, frames


def run_replay_node(
    node_id: str,
    scene_path: str,
    orchestrator_url: str,
    db_path: str,
    triggers: TriggerClient,
    speed: float = 1.0,
    loop: bool = False,
) -> None:
    from expanso_security_camera.sensor.main import emit_json

    header, frames = load_scene(scene_path)
    model_name = str(header.get("model", "recorded"))
    emitter = Emitter(node_id, db_path, orchestrator_url)
    analyst = Analyst(node_id)

    log(f"[{node_id}] replaying {len(frames)} recorded frames from {scene_path} at {speed}x")

    while True:
        started = time.time()

        for frame in frames:
            wait = started + float(frame["offset_s"]) / speed - time.time()

            if wait > 0:
                time.sleep(wait)

            ts = time.time()
            raw = [
                (str(d["label"]), float(d["confidence"]), tuple(float(v) for v in d["bbox"]))
                for d in frame["detections"]
            ]
            hits = select_hits(node_id, triggers, raw)

            if hits:
                event = build_event(node_id, ts, hits, model_name, analyst)
            else:
                event = Event(
                    node=node_id,
                    ts=ts,
                    yolo_hits=[],
                    gemini_description=None,
                    model_versions={"yolo": model_name, "gemini": None},
                )

            sign_event(event)
            emitter.emit(event)
            emit_json(event)

        if not loop:
            break

    # Let the SQLite queue finish any push that was in flight.
    emitter.close()
