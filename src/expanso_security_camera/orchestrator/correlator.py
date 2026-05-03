"""Cross-sensor correlator with explicit alert rules.

Per the demo's narrative: an "alert event" (a.k.a. fused event) fires only
when one of these explicit conditions is met, NOT on every quiet
co-occurrence between two sectors. This keeps the dashboard's FUSION
banner meaningful — when judges see the takeover, it should mean
something the eye would also flag.

Rules:
    1. Single-sensor person+backpack — one frame from either sensor
       containing both labels in the latest event.
    2. Cross-sensor simultaneous-person — a person on each of north
       AND south within WINDOW_SEC (with neither being a queued-offline
       replay, so wall-clock proximity isn't faked by a queue drain).

Replayed offline events are excluded from rule 2 explicitly. Both rules
share a single COOLDOWN_SEC after firing.
"""

from __future__ import annotations

from typing import Optional


# Window for rule 2 (cross-sensor co-occurrence). Empty/heartbeat events
# are written to the store too, so the lookback can return any event —
# we filter for the labels we want inside the rule.
WINDOW_SEC = 5.0
COOLDOWN_SEC = 8.0


def _has_label(event: dict, label: str) -> bool:
    return any(h.get("label") == label for h in event.get("yolo_hits", []))


class Correlator:
    def __init__(self, store) -> None:
        self.store = store
        self._last_fused_ts = 0.0

    def evaluate(self, latest_event: dict) -> Optional[dict]:
        # Replayed events skip both rules. Rule 1 because a person+backpack
        # frame from 30 minutes ago shouldn't surprise the operator now;
        # rule 2 because wall-clock proximity is what makes "two sensors
        # at once" meaningful.
        if latest_event.get("queued_offline"):
            return None

        now = latest_event["ts"]
        if now - self._last_fused_ts < COOLDOWN_SEC:
            return None

        # ── Rule 1: person + backpack on a single frame ──────────────
        if _has_label(latest_event, "person") and _has_label(latest_event, "backpack"):
            fused = {
                "type": "multi_sector_correlation",
                "rule": "person_with_backpack",
                "ts": now,
                "sectors": [latest_event["node"]],
                "contacts": [
                    {
                        "sector": latest_event["node"],
                        "yolo_hits": [
                            h["label"] for h in latest_event.get("yolo_hits", [])
                        ],
                        "description": latest_event.get("gemini_description"),
                    }
                ],
            }
            self._last_fused_ts = now
            return fused

        # ── Rule 2: simultaneous person on north AND south ────────────
        # Only relevant if THIS event has a person — otherwise nothing to
        # correlate against the other sector.
        if not _has_label(latest_event, "person"):
            return None

        recent = self.store.recent(since_ts=now - WINDOW_SEC, limit=100)
        # Look for the OTHER sector having a person hit, also non-replay.
        other = next(
            (
                e
                for e in recent
                if e["node"] != latest_event["node"]
                and not e.get("queued_offline")
                and _has_label(e, "person")
            ),
            None,
        )
        if other is None:
            return None

        sectors = sorted({latest_event["node"], other["node"]})
        fused = {
            "type": "multi_sector_correlation",
            "rule": "person_cross_sector",
            "ts": now,
            "sectors": sectors,
            "contacts": [
                {
                    "sector": e["node"],
                    "yolo_hits": [h["label"] for h in e.get("yolo_hits", [])],
                    "description": e.get("gemini_description"),
                }
                for e in (latest_event, other)
            ],
        }
        self._last_fused_ts = now
        return fused
