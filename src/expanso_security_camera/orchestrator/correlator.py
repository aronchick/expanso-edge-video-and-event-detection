"""Cross-sensor correlator.

Per HACKATHON_SCRIPT.md §9.2. When two or more sectors fire within a short
window, surface a fused event. Replayed offline events are explicitly
excluded so wall-clock proximity isn't distorted by queue drains.
"""

from __future__ import annotations

from typing import Optional

WINDOW_SEC = 5.0
COOLDOWN_SEC = 8.0


class Correlator:
    def __init__(self, store) -> None:
        self.store = store
        self._last_fused_ts = 0.0

    def evaluate(self, latest_event: dict) -> Optional[dict]:
        if latest_event.get("queued_offline"):
            return None

        now = latest_event["ts"]
        if now - self._last_fused_ts < COOLDOWN_SEC:
            return None

        recent = self.store.recent(since_ts=now - WINDOW_SEC, limit=50)
        sectors = {e["node"] for e in recent if e["node"] != latest_event["node"]}
        sectors.add(latest_event["node"])

        if len(sectors) < 2:
            return None

        per_sector: dict[str, dict] = {}
        for e in recent:
            if e["node"] not in per_sector:
                per_sector[e["node"]] = e
        per_sector.setdefault(latest_event["node"], latest_event)

        fused = {
            "type": "multi_sector_correlation",
            "ts": now,
            "sectors": sorted(sectors),
            "contacts": [
                {
                    "sector": e["node"],
                    "yolo_hits": [h["label"] for h in e.get("yolo_hits", [])],
                    "description": e.get("gemini_description"),
                }
                for e in per_sector.values()
            ],
        }
        self._last_fused_ts = now
        return fused
