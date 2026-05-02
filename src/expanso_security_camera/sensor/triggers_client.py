"""Sensor-side trigger config client.

Polls the orchestrator's /triggers endpoint periodically and caches the
result. The detector reads from this cache, so a curl/UI update on the
orchestrator propagates to all sensors within the poll interval — that's
the soft-path live class update demo beat.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

DEFAULT_TRIGGERS = [
    # Trimmed — aerial contacts (airplane, drone) intentionally excluded,
    # added live mid-demo per HACKATHON_SCRIPT.md §13 Beat 3.
    "person",
    "backpack",
    "knife",
    "car",
    "truck",
]
POLL_INTERVAL_SEC = 1.0


class TriggerClient:
    def __init__(self, orchestrator_url: str, fallback: list[str] | None = None) -> None:
        self.orchestrator_url = orchestrator_url.rstrip("/")
        self._triggers = set(fallback or DEFAULT_TRIGGERS)
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def get(self) -> set[str]:
        with self._lock:
            return set(self._triggers)

    def contains(self, label: str) -> bool:
        with self._lock:
            return label in self._triggers

    def _poll_loop(self) -> None:
        while not self._stop:
            self._poll_once()
            time.sleep(POLL_INTERVAL_SEC)

    def _poll_once(self) -> None:
        try:
            req = urllib.request.Request(
                f"{self.orchestrator_url}/triggers",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if not (200 <= resp.status < 300):
                    return
                data = json.loads(resp.read().decode("utf-8"))
            triggers = data.get("triggers", [])
            if isinstance(triggers, list):
                with self._lock:
                    self._triggers = set(str(t) for t in triggers)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            # Orchestrator unreachable — keep last known triggers.
            return

    def close(self) -> None:
        self._stop = True
