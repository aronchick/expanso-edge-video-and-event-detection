"""The decision half of the detection cascade, free of heavy imports.

Takes raw (label, confidence, bbox) detections and decides what the sensor
reports: the airplane-as-drone mapping, the live trigger list, the per-class
confidence floors, and the rate-limited analyst summary through the demo-kit
model gateway (cached description when the gateway is unreachable).

The live YOLO detector and the replay source both call this module, so they
take identical decisions on identical detections.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, Optional

from expanso_security_camera.model_gateway import ask as ask_model_gateway
from expanso_security_camera.sensor.schema import Detection, Event
from expanso_security_camera.sensor.triggers_client import TriggerClient

CONF_THRESHOLD = 0.55
# Per-class overrides for `drone-3class-v2.pt` (Hetzner fine-tune,
# mAP50=0.909 / mAP50-95=0.843 on val).
PER_CLASS_THRESHOLDS: dict[str, float] = {
    # 0.75 was tuned for a venue with full-body foot traffic crossing
    # the FOV — false positives on shadows/posters needed suppression.
    # For the laptop desk-cam demo, the user is OFTEN partially in
    # frame (head cropped, legs cropped, torso-only) and YOLO confidence
    # drops to 0.30-0.55 on partial bodies. Drop to 0.40 so partial
    # detections actually surface. Re-tune up at venue deployments if
    # the long-tail false positives come back.
    "person": 0.40,
    # Same partial-frame logic: a backpack half-occluded by a chair
    # back or carried at the side often comes through at 0.35-0.45.
    "backpack": 0.30,
    # Drone stays HIGH. Training data is sparse enough that round/
    # elongated background objects (light fixtures, ceiling vents)
    # occasionally pull a high-confidence drone label. 0.85 allows
    # real airborne hits while suppressing those.
    "drone": 0.85,
}


def threshold_for(label: str) -> float:
    return PER_CLASS_THRESHOLDS.get(label, CONF_THRESHOLD)


# Per-sensor Gemini cooldown. With GPU YOLO at ~18 events/sec/sensor, a 3s
# cooldown still lets ~40 cloud reachbacks/min through — too noisy for a
# demo (and burns API quota). 15s caps each sensor at 4/min, so the audience
# sees the cloud-reachback pill tick at a calm cadence (~8/min total across
# both sensors) instead of a firehose. Override per-deploy with EDGE_GEMINI_COOLDOWN_SEC.
GEMINI_COOLDOWN_SEC = float(os.environ.get("EDGE_GEMINI_COOLDOWN_SEC", "15.0"))
ANALYST_MODEL = "model-gateway"

ANALYST_SYSTEM = """You summarize local object detections for a perimeter
camera. Return one short sentence. Use only the labels provided. Do not infer
appearance, posture, intent, identity, or anything that local detection did
not establish."""

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


def log(message: str) -> None:
    """Diagnostics go to stderr: stdout carries only the JSON event stream
    that the Expanso pipeline reads."""
    print(message, file=sys.stderr, flush=True)


def select_hits(
    node_id: str,
    triggers: TriggerClient,
    raw: Iterable[tuple[str, float, tuple[float, float, float, float]]],
) -> list[Detection]:
    """Apply the airplane-as-drone mapping, the live trigger list and the
    per-class confidence floors to raw (label, confidence, bbox) detections.

    Shared by the live detector and the replay source so both take the same
    decisions on the same detections."""
    hits: list[Detection] = []

    for label, confidence, bbox in raw:
        if label == "airplane":
            label = "drone"

        thresh = threshold_for(label)
        in_trig = triggers.contains(label)
        passed = in_trig and confidence > thresh
        verdict = "PASS" if passed else ("low-conf" if in_trig else "off-trigger")

        log(
            f"[{node_id}] yolo: {label:9s} conf={confidence:.2f} "
            f"(thresh={thresh:.2f}, trigger={in_trig}, verdict={verdict})"
        )

        if passed:
            hits.append(Detection(label=label, confidence=confidence, bbox=bbox))

    return hits


class Analyst:
    """Rate-limited analyst summary through the demo-kit model gateway, with
    the cached description as the offline fallback."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self._last_ts = 0.0

    def describe(self, hits: list[Detection], ts: float) -> tuple[Optional[str], bool]:
        """Return (description, used_gateway)."""
        if ts - self._last_ts < GEMINI_COOLDOWN_SEC:
            return None, False

        try:
            labels = sorted({hit.label for hit in hits})
            prompt = "Detected object labels: " + ", ".join(labels) + "."
            fixture = "scene-" + "-".join(labels)
            result = ask_model_gateway(
                prompt,
                system=ANALYST_SYSTEM,
                fixture=fixture,
                timeout=1.5,
            )
            text = str(result["text"]).strip()
            self._last_ts = ts

            return text, True
        except Exception as e:
            # CRITICAL: update the cooldown timestamp even on failure, or every
            # detection would re-attempt the gateway and block the YOLO worker
            # until its timeout.
            self._last_ts = ts
            log(f"[{self.node_id}] model gateway unavailable: {e}")

            return _canned_for(hits), False


def build_event(
    node_id: str, ts: float, hits: list[Detection], model_name: str, analyst: Analyst
) -> Event:
    description, used_gateway = analyst.describe(hits, ts)

    return Event(
        node=node_id,
        ts=ts,
        yolo_hits=hits,
        gemini_description=description,
        model_versions={
            "yolo": model_name,
            "gemini": ANALYST_MODEL if used_gateway else None,
        },
    )
