"""Fresh-frame RTSP reader.

Per HACKATHON_SCRIPT.md §8.1. OpenCV's VideoCapture buffers RTSP frames
internally; on a network with any latency this stacks up to multiple
seconds of lag. This wrapper runs the read loop in its own thread,
always overwriting the latest frame, so .read() returns the most
recent frame and nothing else.

On the Jetson we want the GStreamer hardware-decode pipeline
(nvv4l2decoder). Off the Jetson, fall back to the default FFmpeg
backend so this code is testable on a Mac.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import cv2
import numpy as np


def reolink_gst_pipeline(rtsp_url: str) -> str:
    """GStreamer pipeline using Jetson NVMM hardware decode.

    `latency=100` caps the RTSP buffer at 100ms (default 2000ms is
    demo-killing). `protocols=tcp` forces TCP transport — UDP loses
    packets on noisy venue networks. `drop=true max-buffers=1` always
    grabs the freshest frame, dropping older ones.
    """
    return (
        f"rtspsrc location={rtsp_url} latency=100 protocols=tcp ! "
        "rtph264depay ! h264parse ! "
        "nvv4l2decoder ! "
        "nvvidconv ! video/x-raw,format=BGRx ! "
        "videoconvert ! video/x-raw,format=BGR ! "
        "appsink drop=true max-buffers=1 sync=false"
    )


def as_device_index(source: str | int) -> int | None:
    """Return the integer device index if `source` names a local USB
    webcam (an int, or a digit-only string like "0"/"1"), else None.

    The booth setup is two USB-C webcams plugged into a MacBook, addressed
    by capture-device index — NOT RTSP. `esc-test-cameras --list` prints
    the indices that open; pass them with `--cameras 0,1`.
    """
    if isinstance(source, int):
        return source
    s = str(source).strip()
    return int(s) if s.isdigit() else None


# Webcam capture defaults. Two parallel webcam streams are cheap compared to
# two 4K RTSP feeds, but we still cap at 1280x720@30 so the bbox overlay math
# (which reads the actual frame size) stays predictable and the JPEG encode
# in the snapshot loop is fast. Override with EDGE_CAM_WIDTH/HEIGHT/FPS.
_CAM_W = int(os.environ.get("EDGE_CAM_WIDTH", "1280"))
_CAM_H = int(os.environ.get("EDGE_CAM_HEIGHT", "720"))
_CAM_FPS = int(os.environ.get("EDGE_CAM_FPS", "30"))


def _open_capture(source: str | int, prefer_gstreamer: bool) -> cv2.VideoCapture:
    # ── Local USB webcam path (the Mac booth setup) ──────────────────
    index = as_device_index(source)
    if index is not None:
        # No backend hint → OpenCV picks the platform default (AVFoundation
        # on macOS, V4L2 on Linux). Passing CAP_FFMPEG here would try to
        # treat "0" as a file path and fail.
        cap = cv2.VideoCapture(index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, _CAM_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, _CAM_H)
        cap.set(cv2.CAP_PROP_FPS, _CAM_FPS)
        # Smallest buffer so .read() returns the freshest frame, not a
        # backlog — matches the fresh-frame contract for RTSP below.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    # ── RTSP / network path (Jetson + IP cameras) ────────────────────
    rtsp_url = str(source)
    if prefer_gstreamer:
        cap = cv2.VideoCapture(reolink_gst_pipeline(rtsp_url), cv2.CAP_GSTREAMER)
        if cap.isOpened():
            return cap
    # CRITICAL: set OPENCV_FFMPEG_CAPTURE_OPTIONS BEFORE constructing the
    # VideoCapture so the FFmpeg backend uses TCP (RTSP-over-UDP loses
    # packets on flaky links and freezes for 30s waiting for keyframes).
    # Setting via env at the OS level isn't reliable across shell wrappers;
    # do it explicitly in-process here.
    import os as _os

    _os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        "rtsp_transport;tcp"
        "|stimeout;5000000"  # 5s read timeout (default is 30s)
        "|max_delay;500000"  # 500ms reorder buffer
    )
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if "rtsp" in rtsp_url.lower():
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class FreshFrameReader:
    def __init__(
        self,
        rtsp_url: str | int,
        name: str = "cam",
        prefer_gstreamer: Optional[bool] = None,
    ) -> None:
        self.name = name
        self.rtsp_url = rtsp_url
        # USB webcams never want the Jetson GStreamer pipeline. Only auto-
        # enable GStreamer for RTSP sources on Linux (Jetson + IP cameras).
        is_webcam = as_device_index(rtsp_url) is not None
        if prefer_gstreamer is None:
            prefer_gstreamer = (not is_webcam) and os.uname().sysname == "Linux"
        self.prefer_gstreamer = prefer_gstreamer

        self.cap = _open_capture(rtsp_url, prefer_gstreamer)
        if not self.cap.isOpened():
            raise RuntimeError(f"[{name}] failed to open {rtsp_url}")

        self._frame: Optional[np.ndarray] = None
        self._frame_ts: float = 0.0
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(
            target=self._reader_loop, daemon=True, name=f"reader-{name}"
        )
        self._thread.start()

    def _reader_loop(self) -> None:
        consecutive_failures = 0
        while not self._stop:
            ok, frame = self.cap.read()
            if not ok:
                consecutive_failures += 1
                if consecutive_failures > 30:
                    print(f"[{self.name}] stream dead, reconnecting", flush=True)
                    self.cap.release()
                    time.sleep(1)
                    self.cap = _open_capture(self.rtsp_url, self.prefer_gstreamer)
                    consecutive_failures = 0
                continue
            consecutive_failures = 0
            with self._lock:
                self._frame = frame
                self._frame_ts = time.time()

    def read(self) -> Optional[tuple[np.ndarray, float]]:
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy(), self._frame_ts

    def close(self) -> None:
        self._stop = True
        try:
            self.cap.release()
        except Exception:
            pass
