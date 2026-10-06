"""Two-stage detection cascade per HACKATHON_SCRIPT.md §8.2.

Stage 1: local YOLO (TensorRT engine on Jetson, .pt fallback elsewhere).
         Always on, sub-50ms per frame on Orin.
Stage 2: a recorded analyst summary from the demo-kit model gateway. It fires
only on YOLO hits and is rate-limited per node.

The class set that triggers Gemini is read from the orchestrator at
runtime via TriggerClient — that's the live-update knob for the demo.

The gateway sees stable detection labels, not frames. Per-frame vision stays
local; fixture mode is the default and live recording is an explicit operator
action behind the gateway's single-flight guard, caps, and kill switch.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import cv2
import numpy as np

from expanso_security_camera.sensor.cascade import (
    Analyst,
    build_event,
    select_hits,
    threshold_for,
)
from expanso_security_camera.sensor.schema import Detection, Event
from expanso_security_camera.sensor.triggers_client import TriggerClient

# Labels the cascade actually cares about. Derived to indices at runtime
# from `self.model.names`, so this works against both the off-the-shelf
# COCO yolov8s engine ("airplane" included as a drone proxy until we have
# a venue-fine-tuned model) and the 3-class fine-tune ({person, backpack,
# drone}). Adding labels here is a no-op for models that don't expose
# them — the index list just shrinks.
_WANTED_LABELS = {"person", "backpack", "drone", "airplane"}


class Detector:
    def __init__(
        self,
        node_id: str,
        triggers: TriggerClient,
        model_path: str = "yolo11s.engine",
        drone_model_path: str | None = None,
    ) -> None:
        from ultralytics import YOLO  # heavy import, defer until construction

        self.node_id = node_id
        self.triggers = triggers
        self.model = YOLO(model_path)
        # Optional secondary engine. Use case: COCO yolov8s as primary
        # (dense, reliable person/backpack), fine-tuned 3-class as secondary
        # ONLY for the `drone` class (COCO has no drone class — yolov8s
        # emits "airplane" as a proxy, but a real drone-trained model is
        # tighter for the demo's drone-detection beat). Per-frame cost is
        # roughly doubled, but Jetson Orin Nano fits two TRT inferences
        # well under our 100ms / 10 FPS budget.
        self.drone_model = YOLO(drone_model_path) if drone_model_path else None
        # Warm up so first real frame doesn't pay the cold-start tax.
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        self.model(dummy, verbose=False)
        if self.drone_model is not None:
            self.drone_model(dummy, verbose=False)

        self.analyst = Analyst(node_id)
        self.model_name = os.path.basename(model_path).split(".")[0]

    def detect(self, frame: np.ndarray, ts: float) -> Optional[Event]:
        # Derive the class-index list from the model's own names dict, not
        # hardcoded COCO indices, so the same detector works against:
        #   - the original COCO yolov8s.engine (names {0:person, 4:airplane,
        #     24:backpack, ...}) — picks indices [0, 4, 24]
        #   - the venue-fine-tuned 3-class engine (names {0:person, 1:backpack,
        #     2:drone}) — picks indices [0, 1, 2]
        # ultralytics still short-circuits NMS + score sorting on the
        # unwanted classes — same TRT post-process speedup as before.
        wanted_indices = [i for i, n in self.model.names.items() if n in _WANTED_LABELS]
        # Pass a low predict-side conf floor so anything the model is willing
        # to emit reaches our per-class threshold filter. Without this, YOLO's
        # default (0.25) silently drops mid-confidence person/drone outputs
        # before we ever see them — and our 0.30 person bar is meaningless if
        # the candidates never arrive.
        results = self.model(frame, verbose=False, classes=wanted_indices or None, conf=0.10)[0]
        hits = select_hits(
            self.node_id,
            self.triggers,
            (
                (self.model.names[int(cls_idx)], float(conf), tuple(float(v) for v in box))
                for cls_idx, conf, box in zip(
                    results.boxes.cls, results.boxes.conf, results.boxes.xyxy
                )
            ),
        )

        # Secondary pass: fine-tuned drone model. Only consume its `drone`
        # class — person/backpack come from primary (COCO), which is denser
        # and better-calibrated in deployment scenes. Skipped if no
        # drone_model_path was provided at construction.
        if self.drone_model is not None and self.triggers.contains("drone"):
            drone_indices = [i for i, n in self.drone_model.names.items() if n == "drone"]
            if drone_indices:
                drone_results = self.drone_model(
                    frame, verbose=False, classes=drone_indices, conf=0.10
                )[0]
                for cls_idx, conf, box in zip(
                    drone_results.boxes.cls,
                    drone_results.boxes.conf,
                    drone_results.boxes.xyxy,
                ):
                    if self.drone_model.names[int(cls_idx)] != "drone":
                        continue
                    confidence = float(conf)
                    if confidence > threshold_for("drone"):
                        hits.append(
                            Detection(
                                label="drone",
                                confidence=confidence,
                                bbox=tuple(float(v) for v in box),
                            )
                        )

        if not hits:
            return None

        return build_event(self.node_id, ts, hits, self.model_name, self.analyst)

    def annotate(self, frame: np.ndarray, event: Optional[Event]) -> np.ndarray:
        """Return a copy of `frame` with track-gate boxes + labels drawn for
        the detections in `event`. If event is None, returns the frame
        with only the always-on operational overlay (sensor ID, ISO 8601
        UTC timestamp, REC dot, scan tick) so the live snapshot still
        reads as a working sensor feed even between detections.
        """
        out = frame.copy()
        h, w = out.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        text_white = (220, 230, 240)
        text_dim = (160, 175, 190)

        # Always-on overlay: sensor ID + ISO 8601 UTC + REC pulse.
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cv2.putText(out, self.node_id.upper(), (16, 30), font, 0.6, text_white, 1, cv2.LINE_AA)
        cv2.putText(out, iso, (16, 52), font, 0.5, text_dim, 1, cv2.LINE_AA)
        rec_on = (int(time.time() * 2) % 2) == 0
        rec_color = (40, 40, 230) if rec_on else (40, 40, 90)
        cv2.circle(out, (w - 28, 32), 5, rec_color, -1, cv2.LINE_AA)
        cv2.putText(out, "REC", (w - 64, 38), font, 0.45, text_white, 1, cv2.LINE_AA)

        if event is not None:
            # Live per-zone people count, baked top-center so it's readable
            # even on the raw MJPEG feed (before the dashboard tally).
            n_person = sum(1 for hit in event.yolo_hits if hit.label == "person")
            if n_person:
                badge = f"{n_person} PERSON" + ("S" if n_person != 1 else "")
                _draw_count_badge(out, w, badge, _class_color_bgr("person"))
            for hit in event.yolo_hits:
                x1, y1, x2, y2 = (int(v) for v in hit.bbox)
                color = _class_color_bgr(hit.label)
                _draw_track_gate(out, x1, y1, x2, y2, color)
                _draw_label_chip(
                    out, x1, y1, f"{hit.label.upper()} {int(hit.confidence * 100)}%", color
                )

        return out


# Per-class colors (BGR). Kept in sync with snapshots._CLASS_COLOR_BGR and
# the dashboard's _CLASS_COLORS: person = green, backpack = amber, drone = red.
_CLASS_COLOR_BGR: dict[str, tuple] = {
    "person": (90, 230, 60),
    "backpack": (38, 167, 255),
    "drone": (60, 60, 240),
    "airplane": (60, 60, 240),
}


def _class_color_bgr(label: str) -> tuple:
    return _CLASS_COLOR_BGR.get(str(label).lower(), (38, 167, 255))


def _draw_track_gate(img: np.ndarray, x1: int, y1: int, x2: int, y2: int, color: tuple) -> None:
    """Thin full rectangle + thick corner brackets — bold enough to read on a
    42" booth monitor. Matches the orchestrator snapshot synth aesthetic."""
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
    leg = max(18, min((x2 - x1) // 4, (y2 - y1) // 4, 40))
    t = 4
    for cx_, cy_, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
        cv2.line(img, (cx_, cy_), (cx_ + dx * leg, cy_), color, t, cv2.LINE_AA)
        cv2.line(img, (cx_, cy_), (cx_, cy_ + dy * leg), color, t, cv2.LINE_AA)


def _draw_label_chip(img: np.ndarray, x1: int, y1: int, text: str, color: tuple) -> None:
    """Filled label chip with dark text — legible at booth distance."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 0.7, 2
    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    pad = 8
    cy2 = max(y1, th + 2 * pad + 2)
    cv2.rectangle(img, (x1, cy2 - th - 2 * pad), (x1 + tw + 2 * pad, cy2), color, -1, cv2.LINE_AA)
    cv2.putText(img, text, (x1 + pad, cy2 - pad), font, scale, (20, 24, 28), thick, cv2.LINE_AA)


def _draw_count_badge(img: np.ndarray, w: int, text: str, color: tuple) -> None:
    """Top-center filled badge showing the live person count for this zone."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 1.0, 2
    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    pad = 12
    x1 = (w - tw) // 2 - pad
    cv2.rectangle(img, (x1, 12), (x1 + tw + 2 * pad, 12 + th + 2 * pad), color, -1, cv2.LINE_AA)
    cv2.putText(img, text, (x1 + pad, 12 + th + pad), font, scale, (20, 24, 28), thick, cv2.LINE_AA)
