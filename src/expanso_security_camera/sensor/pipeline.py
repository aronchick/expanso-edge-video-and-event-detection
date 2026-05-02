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


def _open_capture(rtsp_url: str, prefer_gstreamer: bool) -> cv2.VideoCapture:
    if prefer_gstreamer:
        cap = cv2.VideoCapture(reolink_gst_pipeline(rtsp_url), cv2.CAP_GSTREAMER)
        if cap.isOpened():
            return cap
    cap = cv2.VideoCapture(rtsp_url)
    if "rtsp" in rtsp_url.lower():
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class FreshFrameReader:
    def __init__(
        self,
        rtsp_url: str,
        name: str = "cam",
        prefer_gstreamer: Optional[bool] = None,
    ) -> None:
        self.name = name
        self.rtsp_url = rtsp_url
        # Auto-detect: assume GStreamer on Linux (Jetson), software decode elsewhere.
        if prefer_gstreamer is None:
            prefer_gstreamer = os.uname().sysname == "Linux"
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
