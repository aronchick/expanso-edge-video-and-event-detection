"""Two-stage detection cascade per HACKATHON_SCRIPT.md §8.2.

Stage 1: local YOLO (TensorRT engine on Jetson, .pt fallback elsewhere).
         Always on, sub-50ms per frame on Orin.
Stage 2: Gemini Flash. Fires only on YOLO hits, rate-limited per node.

The class set that triggers Gemini is read from the orchestrator at
runtime via TriggerClient — that's the live-update knob for the demo.

Gemini calls go through urllib.request to match the existing
dataset.py pattern (no SDK dependency). On any failure (timeout, 429,
network), we fall back to a canned description per Appendix A so the
demo keeps flowing.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

import cv2
import numpy as np

from expanso_security_camera.sensor.schema import Detection, Event
from expanso_security_camera.sensor.triggers_client import TriggerClient

CONF_THRESHOLD = 0.55
# Per-class overrides. The 3-class fine-tune trained to mAP50=0.995 on
# `person` (we can lower its bar safely: virtually no false positives).
# `drone` only got mAP50=0.193 from sparse footage, so we also lower its bar
# to surface anything the model thinks looks droney. `backpack` sits at
# mAP50=0.922 but at this camera angle real backpacks frequently land in
# the 0.5–0.6 band; 0.35 keeps recall high without flooding the dashboard.
PER_CLASS_THRESHOLDS: dict[str, float] = {
    # COCO yolov8s emits dense, well-calibrated person/backpack scores in
    # this venue — set the bar high enough to suppress low-conf clutter
    # ("fake people" on furniture / posters / shadow). A separate fine-tune
    # pass on real venue footage is queued for later, which will let us drop
    # these back down with confidence.
    "person": 0.60,
    "backpack": 0.50,
    # Drone is effectively suppressed until the planned Hetzner fine-tune
    # pass produces a properly-trained drone class. The current fine-tune
    # mAP50=0.193 yields too many low-conf false positives (chairs, lamps,
    # ceiling fans) at any threshold the demo would actually fire on.
    # Setting the bar at 0.95 means nothing real fires; resurrect this knob
    # post-retrain.
    "drone": 0.95,
}


def _threshold_for(label: str) -> float:
    return PER_CLASS_THRESHOLDS.get(label, CONF_THRESHOLD)
# Labels the cascade actually cares about. Derived to indices at runtime
# from `self.model.names`, so this works against both the off-the-shelf
# COCO yolov8s engine ("airplane" included as a drone proxy until we have
# a venue-fine-tuned model) and the 3-class fine-tune ({person, backpack,
# drone}). Adding labels here is a no-op for models that don't expose
# them — the index list just shrinks.
_WANTED_LABELS = {"person", "backpack", "drone", "airplane"}

# Per-sensor Gemini cooldown. With GPU YOLO at ~18 events/sec/sensor, a 3s
# cooldown still lets ~40 cloud reachbacks/min through — too noisy for a
# demo (and burns API quota). 15s caps each sensor at 4/min, so the audience
# sees the cloud-reachback pill tick at a calm cadence (~8/min total across
# both sensors) instead of a firehose. Override per-deploy with EDGE_GEMINI_COOLDOWN_SEC.
GEMINI_COOLDOWN_SEC = float(os.environ.get("EDGE_GEMINI_COOLDOWN_SEC", "15.0"))
GEMINI_MODEL = "gemini-3-flash-preview"

GEMINI_PROMPT = """You are an edge sensor analyst. Look at this frame from
a perimeter camera and respond in ONE short sentence covering:
- what is visible (objects, persons, vehicles)
- any tactically relevant details (carried items, posture, vehicle type)
- whether this differs from a typical civilian scene

Do not speculate beyond what is visible. If nothing notable is in frame,
say "no notable activity"."""

# Canned descriptions for graceful Gemini-down fallback (Appendix A).
CANNED_DESCRIPTIONS: dict[tuple[str, ...] | str, str] = {
    "person": "Adult, ambulatory, in frame.",
    ("backpack", "person"): "Adult carrying pack, posture suggests load.",
    ("cell phone", "person"): "Adult holding handheld electronic device.",
    "airplane": "Small aerial vehicle, low altitude, quadcopter form.",
    "drone": "Small aerial vehicle, low altitude, quadcopter form.",
    "truck": "Vehicle in frame, light transport class.",
    "car": "Vehicle in frame, passenger class.",
    "knife": "Bladed object in frame.",
}


def _canned_for(hits: list[Detection]) -> Optional[str]:
    """Return a cached description keyed by detected classes, or None."""
    if not hits:
        return None
    labels = tuple(sorted({h.label for h in hits}))
    if labels in CANNED_DESCRIPTIONS:
        return f"{CANNED_DESCRIPTIONS[labels]} [cached]"
    if labels[0] in CANNED_DESCRIPTIONS:
        return f"{CANNED_DESCRIPTIONS[labels[0]]} [cached]"
    return None


class Detector:
    def __init__(
        self,
        node_id: str,
        triggers: TriggerClient,
        model_path: str = "yolo11s.engine",
        drone_model_path: str | None = None,
        gemini_api_key: str | None = None,
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

        self.api_key = (
            gemini_api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY", "")
        )
        self._last_gemini_ts = 0.0
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
        wanted_indices = [
            i for i, n in self.model.names.items() if n in _WANTED_LABELS
        ]
        # Pass a low predict-side conf floor so anything the model is willing
        # to emit reaches our per-class threshold filter. Without this, YOLO's
        # default (0.25) silently drops mid-confidence person/drone outputs
        # before we ever see them — and our 0.30 person bar is meaningless if
        # the candidates never arrive.
        results = self.model(
            frame, verbose=False, classes=wanted_indices or None, conf=0.10
        )[0]
        hits: list[Detection] = []
        for cls_idx, conf, box in zip(results.boxes.cls, results.boxes.conf, results.boxes.xyxy):
            label = self.model.names[int(cls_idx)]
            # COCO has no drone class — yolov8s emits "airplane" for civilian
            # quadcopters. The dashboard's displayLabel() already aliases
            # this, but the trigger filter compares raw labels, so the alias
            # has to happen here too. Once the fine-tuned 3-class engine is
            # loaded, real "drone" hits flow through with no remap.
            if label == "airplane":
                label = "drone"
            confidence = float(conf)
            if self.triggers.contains(label) and confidence > _threshold_for(label):
                hits.append(
                    Detection(
                        label=label,
                        confidence=confidence,
                        bbox=tuple(float(v) for v in box),
                    )
                )

        # Secondary pass: fine-tuned drone model. Only consume its `drone`
        # class — person/backpack come from primary (COCO), which is denser
        # and better-calibrated in deployment scenes. Skipped if no
        # drone_model_path was provided at construction.
        if self.drone_model is not None and self.triggers.contains("drone"):
            drone_indices = [
                i for i, n in self.drone_model.names.items() if n == "drone"
            ]
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
                    if confidence > _threshold_for("drone"):
                        hits.append(
                            Detection(
                                label="drone",
                                confidence=confidence,
                                bbox=tuple(float(v) for v in box),
                            )
                        )

        if not hits:
            return None

        gemini_desc, gemini_used = self._maybe_describe(frame, hits, ts)

        return Event(
            node=self.node_id,
            ts=ts,
            yolo_hits=hits,
            gemini_description=gemini_desc,
            model_versions={
                "yolo": self.model_name,
                "gemini": GEMINI_MODEL if gemini_used else None,
            },
        )

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
        amber = (38, 167, 255)  # BGR for #ffa726
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
            for i, hit in enumerate(event.yolo_hits):
                x1, y1, x2, y2 = (int(v) for v in hit.bbox)
                _draw_track_gate(out, x1, y1, x2, y2, amber)
                label_text = f"TRK-{i + 1:03d} {hit.label.upper()} {int(hit.confidence * 100)}"
                cv2.putText(
                    out, label_text, (x1, max(y1 - 8, 18)), font, 0.55, amber, 1, cv2.LINE_AA
                )

        return out

    def _maybe_describe(
        self, frame: np.ndarray, hits: list[Detection], ts: float
    ) -> tuple[Optional[str], bool]:
        """Return (description, used_gemini)."""
        if ts - self._last_gemini_ts < GEMINI_COOLDOWN_SEC:
            return None, False
        if not self.api_key:
            return _canned_for(hits), False

        try:
            _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            text = _call_gemini(self.api_key, jpg.tobytes())
            self._last_gemini_ts = ts
            return text, True
        except Exception as e:
            # CRITICAL: update the cooldown timestamp even on failure. Otherwise,
            # when WAN is down, every detection immediately re-attempts Gemini
            # (since the cooldown check sees a stale `_last_gemini_ts`), and
            # the synchronous urllib call blocks the yolo worker for the full
            # timeout on EACH detection. The result is event/bbox stutter that
            # tracks the timeout cadence, not the cooldown. (Beat 5A bug.)
            self._last_gemini_ts = ts
            print(f"[{self.node_id}] gemini call failed: {e}", flush=True)
            return _canned_for(hits), False


def _call_gemini(api_key: str, jpg_bytes: bytes, timeout: float = 1.5) -> str:
    """Single-shot Gemini call. Retries 429 with exponential backoff.

    Timeout is intentionally short (1.5s) — when the WAN is healthy, Gemini
    Flash returns in 200-800ms; when the WAN is down, we'd rather give up fast
    than block the synchronous yolo worker (which would visibly stutter the
    bbox overlay and event stream). The cooldown still throttles attempt rate.
    """
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )
    payload = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": base64.b64encode(jpg_bytes).decode("utf-8"),
                            }
                        },
                        {"text": GEMINI_PROMPT},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 256},
        }
    ).encode("utf-8")

    for attempt in range(4):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return text.strip()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(2**attempt)
                continue
            raise


def _draw_track_gate(img: np.ndarray, x1: int, y1: int, x2: int, y2: int, color: tuple) -> None:
    """L-shaped corner brackets — track-gate style, matches the orchestrator
    snapshot synth aesthetic. Module-level so the class boundary stays clean."""
    leg = max(12, min((x2 - x1) // 5, (y2 - y1) // 5, 24))
    # Top-left
    cv2.line(img, (x1, y1), (x1 + leg, y1), color, 2, cv2.LINE_AA)
    cv2.line(img, (x1, y1), (x1, y1 + leg), color, 2, cv2.LINE_AA)
    # Top-right
    cv2.line(img, (x2, y1), (x2 - leg, y1), color, 2, cv2.LINE_AA)
    cv2.line(img, (x2, y1), (x2, y1 + leg), color, 2, cv2.LINE_AA)
    # Bottom-left
    cv2.line(img, (x1, y2), (x1 + leg, y2), color, 2, cv2.LINE_AA)
    cv2.line(img, (x1, y2), (x1, y2 - leg), color, 2, cv2.LINE_AA)
    # Bottom-right
    cv2.line(img, (x2, y2), (x2 - leg, y2), color, 2, cv2.LINE_AA)
    cv2.line(img, (x2, y2), (x2, y2 - leg), color, 2, cv2.LINE_AA)
