"""Camera snapshot serving.

Per DEMO_UI_SPEC.md §4.1. The orchestrator's /snapshot/{sector} endpoint
returns the latest annotated JPEG for a sector. In real mode this is
written to disk by the sensor's detect_loop. In fake mode we synthesize
a 1280x720 JPEG procedurally so the laptop demo has visual feeds.

Synthesis uses opencv (already a dep) so we don't add anything new.
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np


def read_real_snapshot(snapshots_dir: Path, sector: str) -> bytes | None:
    """Return the latest snapshot JPEG bytes for a sector, or None if missing."""
    path = snapshots_dir / f"{sector}.jpg"
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


# Fake-mode scene tables. The snapshot the dashboard polls reflects the
# most recently emitted event for that sector — kept in a tiny in-memory
# cache the orchestrator pokes when an event arrives.

_SECTOR_COLOR = {
    "sensor-north": (95, 70, 30),  # warm
    "sensor-south": (40, 80, 110),  # cool
}


_SENSOR_META = {
    "sensor-north": {
        "id": "EO-N-01",
        "lat": 35.2451,
        "lon": -116.7682,
        "az": 327,
        "fov": 18.0,
    },
    "sensor-south": {
        "id": "IR-S-02",
        "lat": 35.2398,
        "lon": -116.7704,
        "az": 153,
        "fov": 24.0,
    },
}


def synthesize_snapshot(
    sector: str,
    yolo_hits: list[dict] | None = None,
    label: str = "",
    cloud_up: bool = True,
) -> bytes:
    """Render a 1280x720 BGR frame that READS as a surveillance/EO-IR feed:
    horizon-tinted scene, atmospheric noise, scan grid, center reticle,
    corner sensor metadata (sensor ID + DTG + GPS + zoom), bracket-style
    track gates per detection, and a faint REC pulse.

    The composition is deliberately CENTER-WEIGHTED so when the dashboard's
    `object-fit: cover` crops the top/bottom rows, the operator-relevant
    content (reticle, tracks, scan grid) still reaches the viewer.
    """
    h, w = 720, 1280
    cx, cy = w // 2, h // 2

    # ── Scene background: horizon-tinted gradient with atmospheric layers ──
    base_color = _SECTOR_COLOR.get(sector, (60, 60, 60))
    # Build the gradient as a vertical column then broadcast across width.
    col = np.zeros((h, 3), dtype=np.float32)
    for y in range(h):
        # Sky→ground: brighter and slightly desaturated up top, darker down low
        ratio = y / h
        sky_factor = 0.85 - 0.55 * ratio
        col[y] = [c * sky_factor for c in base_color]
    bg = np.broadcast_to(col[:, None, :], (h, w, 3)).copy().astype(np.float32)

    # Atmospheric grain — sparse low-amplitude noise reads as "sensor noise".
    rng = np.random.default_rng(int(time.time() * 4) % (2**31))
    noise = rng.normal(0, 6, (h, w)).astype(np.float32)
    bg += noise[:, :, None]

    # Subtle vignette — corners darken slightly so the eye centers.
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    vignette = 1.0 - 0.35 * np.clip(dist / (0.6 * np.hypot(cx, cy)), 0, 1)
    bg *= vignette[:, :, None]
    bg = np.clip(bg, 0, 255).astype(np.uint8)

    # ── Scan grid (light gray, every 80px) — cheap "sensor overlay" cue ──
    grid_color = (70, 80, 90)
    for x in range(0, w, 80):
        cv2.line(bg, (x, 0), (x, h), grid_color, 1, cv2.LINE_AA)
    for y in range(0, h, 80):
        cv2.line(bg, (0, y), (w, y), grid_color, 1, cv2.LINE_AA)

    # ── Drifting horizontal scan line (single sweep, slow) ──
    t = time.time()
    line_y = int((t * 50) % h)
    cv2.line(bg, (0, line_y), (w, line_y), (180, 200, 215), 1, cv2.LINE_AA)

    # ── Center reticle: crosshair + range ticks + center dot ──
    reticle = (210, 220, 230)
    cv2.line(bg, (cx - 60, cy), (cx - 18, cy), reticle, 1, cv2.LINE_AA)
    cv2.line(bg, (cx + 18, cy), (cx + 60, cy), reticle, 1, cv2.LINE_AA)
    cv2.line(bg, (cx, cy - 60), (cx, cy - 18), reticle, 1, cv2.LINE_AA)
    cv2.line(bg, (cx, cy + 18), (cx, cy + 60), reticle, 1, cv2.LINE_AA)
    # Range ticks
    for r in (90, 140):
        cv2.line(bg, (cx - r - 6, cy), (cx - r, cy), reticle, 1, cv2.LINE_AA)
        cv2.line(bg, (cx + r, cy), (cx + r + 6, cy), reticle, 1, cv2.LINE_AA)
        cv2.line(bg, (cx, cy - r - 6), (cx, cy - r), reticle, 1, cv2.LINE_AA)
        cv2.line(bg, (cx, cy + r), (cx, cy + r + 6), reticle, 1, cv2.LINE_AA)
    cv2.circle(bg, (cx, cy), 2, reticle, -1, cv2.LINE_AA)

    # ── Corner sensor metadata (ISO 8601 UTC + sensor ID + GPS + FOV) ──
    meta = _SENSOR_META.get(sector, {"id": "?", "lat": 0.0, "lon": 0.0, "az": 0, "fov": 0.0})
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    font = cv2.FONT_HERSHEY_SIMPLEX
    text_white = (220, 230, 240)
    text_dim = (160, 175, 190)
    # Top-left: sensor ID + DTG (vertically stacked)
    cv2.putText(bg, meta["id"], (24, 38), font, 0.7, text_white, 1, cv2.LINE_AA)
    cv2.putText(bg, iso, (24, 62), font, 0.55, text_dim, 1, cv2.LINE_AA)
    # Top-right: GPS + azimuth + FOV
    gps = f"{meta['lat']:.4f}N {abs(meta['lon']):.4f}W"
    az_fov = f"AZ {meta['az']:03d}  FOV {meta['fov']:.1f}"
    cv2.putText(bg, gps, (w - 320, 38), font, 0.55, text_dim, 1, cv2.LINE_AA)
    cv2.putText(bg, az_fov, (w - 320, 62), font, 0.55, text_dim, 1, cv2.LINE_AA)
    # Top-right REC indicator (pulses red dot at 1Hz so the feed reads "live")
    rec_on = (int(t * 2) % 2) == 0
    rec_color = (40, 40, 230) if rec_on else (40, 40, 90)  # BGR red
    cv2.circle(bg, (w - 30, 50), 6, rec_color, -1, cv2.LINE_AA)
    cv2.putText(bg, "REC", (w - 70, 56), font, 0.5, text_white, 1, cv2.LINE_AA)

    # ── Track gates: bold, color-coded, with filled label chips so they
    # read from across a conference hall on a 42" monitor. ──
    if yolo_hits:
        # Spread multiple boxes of the SAME label across the frame (so 4
        # "person" hits become 4 distinct gates, not 4 stacked on one spot).
        n_by_label: dict[str, int] = {}
        for hit in yolo_hits:
            lbl = hit.get("label", "object")
            n_by_label[lbl] = n_by_label.get(lbl, 0) + 1
        idx_by_label: dict[str, int] = {}
        for i, hit in enumerate(yolo_hits):
            lbl = hit.get("label", "object")
            li = idx_by_label.get(lbl, 0)
            idx_by_label[lbl] = li + 1
            x1, y1, x2, y2 = _placeholder_bbox(lbl, w, h, li, n_by_label[lbl])
            color = _class_color_bgr(lbl)
            conf_pct = int(hit.get("confidence", 0) * 100)
            _draw_bold_gate(bg, x1, y1, x2, y2, color)
            _draw_label_chip(bg, x1, y1, f"{lbl.upper()} {conf_pct}%", color)

    ok, buf = cv2.imencode(".jpg", bg, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not ok:
        return b""
    return buf.tobytes()


# Per-class colors (BGR for OpenCV). Chosen to POP on the dark EO/IR scene
# and to match the dashboard's per-class palette: person = bright green,
# backpack = amber, drone = red. Kept in sync with _CLASS_COLORS in
# public/edge/ws_client.js.
_CLASS_COLOR_BGR: dict[str, tuple] = {
    "person": (90, 230, 60),  # green  (#3ce65a-ish)
    "backpack": (38, 167, 255),  # amber  (#ffa726)
    "drone": (60, 60, 240),  # red    (#f03c3c)
    "airplane": (60, 60, 240),  # drone proxy
}


def _class_color_bgr(label: str) -> tuple:
    return _CLASS_COLOR_BGR.get(str(label).lower(), (38, 167, 255))


def _draw_bold_gate(img: np.ndarray, x1: int, y1: int, x2: int, y2: int, color: tuple) -> None:
    """Thin full rectangle + thick corner brackets — a track gate that reads
    at a distance on a 42" booth monitor (bracket legs 4px, box edge 2px)."""
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
    leg = max(18, min((x2 - x1) // 4, (y2 - y1) // 4, 40))
    t = 4
    for cx_, cy_, dx, dy in (
        (x1, y1, 1, 1),
        (x2, y1, -1, 1),
        (x1, y2, 1, -1),
        (x2, y2, -1, -1),
    ):
        cv2.line(img, (cx_, cy_), (cx_ + dx * leg, cy_), color, t, cv2.LINE_AA)
        cv2.line(img, (cx_, cy_), (cx_, cy_ + dy * leg), color, t, cv2.LINE_AA)


def _draw_label_chip(img: np.ndarray, x1: int, y1: int, text: str, color: tuple) -> None:
    """Filled rounded-ish label chip above the box with dark text — far more
    legible at booth distance than thin colored text on a noisy scene."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 0.7, 2
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    pad = 8
    cy2 = max(y1, th + 2 * pad + 2)
    top_left = (x1, cy2 - th - 2 * pad)
    bot_right = (x1 + tw + 2 * pad, cy2)
    cv2.rectangle(img, top_left, bot_right, color, -1, cv2.LINE_AA)
    cv2.putText(img, text, (x1 + pad, cy2 - pad), font, scale, (20, 24, 28), thick, cv2.LINE_AA)


def _placeholder_bbox(
    label: str, w: int, h: int, i: int = 0, n: int = 1
) -> tuple[int, int, int, int]:
    """Plausible bbox for synthesized feeds. When a label appears `n` times
    (e.g. a crowd of `n` people), `i` spreads the boxes evenly across the
    frame so the gates don't stack on one spot."""
    bw = int(0.12 * w)
    bh = int(0.42 * h)
    if n <= 1:
        cx = int(0.5 * w)
    else:
        cx = int((0.12 + 0.76 * (i / (n - 1))) * w)
    cy = int((0.54 + 0.05 * ((i % 2) * 2 - 1)) * h)
    return (cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2)


def synthesize_awaiting_start(sector: str) -> bytes:
    """Render a 1280x720 placeholder for a sensor whose Expanso job is
    NOT running (Beat 0 lights-up state, or any time the operator has
    intentionally stopped a sensor between rehearsals).

    Visually distinct from the live `synthesize_snapshot`:
      - Flat gray scene (no horizon tint, no atmospheric noise)
      - No reticle, no scan-line, no track gates, no REC pulse
      - Just sensor metadata in the corner + a single centered line
        "AWAITING START · sensor-X" so judges at 15ft can read it
      - Subdued color reads as "intentionally idle," not "broken"

    The contract: anyone seeing this frame should understand "the
    sensor exists, it has an identity, it just hasn't been started yet."
    """
    h, w = 720, 1280
    cx, cy = w // 2, h // 2

    # Flat dark-gray scene, no gradient, no noise.
    bg = np.full((h, w, 3), 36, dtype=np.uint8)

    # Subtle horizontal rule one-third up to anchor the eye.
    cv2.line(bg, (0, int(h * 0.66)), (w, int(h * 0.66)), (62, 70, 80), 1, cv2.LINE_AA)

    # Big centered headline.
    font = cv2.FONT_HERSHEY_SIMPLEX
    headline = "AWAITING START"
    (tw, th), _ = cv2.getTextSize(headline, font, 2.4, 4)
    cv2.putText(
        bg,
        headline,
        (cx - tw // 2, cy - th // 2 + 30),
        font,
        2.4,
        (180, 195, 210),
        4,
        cv2.LINE_AA,
    )

    # Sub-line: which sector this placeholder is for.
    # NOTE: OpenCV's HERSHEY_SIMPLEX is ASCII-only; the U+00B7 middle dot
    # falls back to "?" which reads as a glitch. Use plain ASCII separator.
    sub = sector.upper().replace("SENSOR-", "SENSOR  ")
    (tw2, _), _ = cv2.getTextSize(sub, font, 0.95, 2)
    cv2.putText(
        bg,
        sub,
        (cx - tw2 // 2, cy + 50),
        font,
        0.95,
        (130, 145, 160),
        2,
        cv2.LINE_AA,
    )

    # Corner sensor metadata (same identity card as the live frame, so a
    # judge comparing the two reads it as "same sensor, different state").
    meta = _SENSOR_META.get(sector, {"id": "?", "lat": 0.0, "lon": 0.0, "az": 0, "fov": 0.0})
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text_white = (220, 230, 240)
    text_dim = (140, 155, 170)
    cv2.putText(bg, meta["id"], (24, 38), font, 0.7, text_white, 1, cv2.LINE_AA)
    cv2.putText(bg, iso, (24, 62), font, 0.55, text_dim, 1, cv2.LINE_AA)
    gps = f"{meta['lat']:.4f}N {abs(meta['lon']):.4f}W"
    cv2.putText(bg, gps, (w - 320, 38), font, 0.55, text_dim, 1, cv2.LINE_AA)
    cv2.putText(bg, "STOPPED", (w - 320, 62), font, 0.55, (90, 100, 115), 1, cv2.LINE_AA)

    # NO REC pulse. NO reticle. NO scan-line. Stillness = stopped.

    ok, buf = cv2.imencode(".jpg", bg, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not ok:
        return b""
    return buf.tobytes()


class FakeSnapshotCache:
    """Holds the latest-event-derived snapshot per sector, regenerated on demand."""

    def __init__(self) -> None:
        self._latest: dict[str, dict] = {}  # sector -> last event dict

    def update(self, event: dict) -> None:
        sector = event.get("node")
        if isinstance(sector, str) and sector.startswith("sensor-"):
            self._latest[sector] = event

    def render(self, sector: str, cloud_up: bool) -> bytes:
        ev = self._latest.get(sector)
        hits = ev.get("yolo_hits") if ev else None
        label = ""
        if ev:
            labels = [h.get("label", "?") for h in (hits or [])]
            label = ", ".join(labels) if labels else ""
        return synthesize_snapshot(sector, hits, label, cloud_up=cloud_up)
