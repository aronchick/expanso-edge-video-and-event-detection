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
GEMINI_COOLDOWN_SEC = 3.0
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
        gemini_api_key: str | None = None,
    ) -> None:
        from ultralytics import YOLO  # heavy import, defer until construction

        self.node_id = node_id
        self.triggers = triggers
        self.model = YOLO(model_path)
        # Warm up so first real frame doesn't pay the cold-start tax.
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        self.model(dummy, verbose=False)

        self.api_key = (
            gemini_api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY", "")
        )
        self._last_gemini_ts = 0.0
        self.model_name = os.path.basename(model_path).split(".")[0]

    def detect(self, frame: np.ndarray, ts: float) -> Optional[Event]:
        results = self.model(frame, verbose=False)[0]
        hits: list[Detection] = []
        for cls_idx, conf, box in zip(results.boxes.cls, results.boxes.conf, results.boxes.xyxy):
            label = self.model.names[int(cls_idx)]
            confidence = float(conf)
            if self.triggers.contains(label) and confidence > CONF_THRESHOLD:
                hits.append(
                    Detection(
                        label=label,
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
            print(f"[{self.node_id}] gemini call failed: {e}", flush=True)
            return _canned_for(hits), False


def _call_gemini(api_key: str, jpg_bytes: bytes, timeout: float = 5.0) -> str:
    """Single-shot Gemini call. Retries 429 with exponential backoff."""
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
