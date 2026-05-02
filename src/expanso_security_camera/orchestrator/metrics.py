"""Rolling metrics for the dashboard footer + header counter.

Per DEMO_UI_SPEC.md §5.1. Maintains a 60s window of event timestamps for
events/min, plus running totals for events, fused alerts, and signed
events.

Also tracks the simulated cloud-up state (per §5.3 / §4.3) so /demo/wan-down
flips a flag the orchestrator's event handler reads.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()
        self.total_events = 0
        self.fused_alerts = 0
        self.signed_events = 0
        self.queued_offline = 0
        self.cloud_up = True
        self._wan_down_at: float | None = None

    def record_event(self, signed: bool, queued_offline: bool) -> None:
        now = time.time()
        with self._lock:
            self._timestamps.append(now)
            self.total_events += 1
            if signed:
                self.signed_events += 1
            if queued_offline:
                self.queued_offline += 1
            self._evict_unlocked(now)

    def record_fused(self) -> None:
        with self._lock:
            self.fused_alerts += 1

    def events_per_minute(self) -> float:
        now = time.time()
        with self._lock:
            self._evict_unlocked(now)
            return float(len(self._timestamps))

    def _evict_unlocked(self, now: float) -> None:
        cutoff = now - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    # ── Demo controls (keyboard shortcuts) ────────────────────────────

    def set_cloud(self, up: bool) -> None:
        with self._lock:
            self.cloud_up = up
            self._wan_down_at = None if up else time.time()

    def is_cloud_up(self) -> bool:
        with self._lock:
            return self.cloud_up

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total_events": self.total_events,
                "fused_alerts": self.fused_alerts,
                "signed_events": self.signed_events,
                "queued_offline": self.queued_offline,
                "events_per_minute": float(len(self._timestamps)),
                "cloud_up": self.cloud_up,
                "wan_down_seconds": (
                    None
                    if self.cloud_up or self._wan_down_at is None
                    else round(time.time() - self._wan_down_at, 1)
                ),
            }
