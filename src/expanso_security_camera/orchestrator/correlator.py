"""Cross-sensor alert correlator.

The dashboard's RED ALERT banner only fires under explicit conditions —
not on every quiet co-occurrence. Three rules:

    1. backpack_detected       — any backpack hit on either sensor,
                                 whether or not a person is in frame.
                                 Backpack is the high-value object —
                                 anyone carrying one near the perimeter
                                 deserves an alert.
    2. multiple_persons        — two or more person hits in a single
                                 event. One person alone is normal foot
                                 traffic; a group is the alert-worthy
                                 signal.
    3. drone_after_update      — a drone detection on either sensor,
                                 BUT only after the operator has rolled
                                 out the drone class (i.e. "drone"
                                 currently appears in the active trigger
                                 set). Pre-update, drone hits don't fire
                                 alerts even if YOLO emits one.

Replayed-offline events skip all rules — wall-clock simultaneity is what
makes "two sensors at once" meaningful, and stale archive replays would
fake it.
"""

from __future__ import annotations

from typing import Optional


WINDOW_SEC = 5.0
COOLDOWN_SEC = 8.0


def _has_label(event: dict, label: str) -> bool:
    return any(h.get("label") == label for h in event.get("yolo_hits", []))


class Correlator:
    def __init__(self, store, triggers=None) -> None:
        """`triggers` is a TriggerStore (or anything with .get() returning
        the active trigger label list). Used by Rule 3 to gate drone
        alerts on the post-update state.
        """
        self.store = store
        self.triggers = triggers
        self._last_alert_ts = 0.0

    def _drone_armed(self) -> bool:
        if self.triggers is None:
            return False
        try:
            return "drone" in self.triggers.get()
        except Exception:
            return False

    def evaluate(self, latest_event: dict) -> Optional[dict]:
        if latest_event.get("queued_offline"):
            return None

        now = latest_event["ts"]
        if now - self._last_alert_ts < COOLDOWN_SEC:
            return None

        # ── Rule 1: any backpack hit ─────────────────────────────────
        # Backpack alone is enough — the value of the alert is "someone
        # is carrying something into the perimeter," and that should
        # trip whether or not YOLO also matched the person who's
        # carrying it (occluded, partially out of frame, etc.).
        if _has_label(latest_event, "backpack"):
            return self._fire(now, "backpack_detected",
                              [latest_event["node"]],
                              [latest_event])

        # ── Rule 3: drone post-pipeline-update ───────────────────────
        # Checked before Rule 2 so a single-sensor drone hit doesn't get
        # short-circuited by needing a cross-sector partner. Only arms
        # once the operator has added "drone" to the active trigger set
        # (Beat 4 in the demo runbook), which is the moment the audience
        # has been told "we're now also looking for drones."
        if self._drone_armed() and _has_label(latest_event, "drone"):
            return self._fire(now, "drone_after_update",
                              [latest_event["node"]],
                              [latest_event])

        # ── Rule 2: 2+ persons in a single event ─────────────────────
        # One person alone is normal foot traffic; multiple people in a
        # single frame is the alert-worthy signal (group walking
        # together past the perimeter).
        person_count = sum(
            1 for h in latest_event.get("yolo_hits", []) if h.get("label") == "person"
        )
        if person_count >= 2:
            return self._fire(now, "multiple_persons",
                              [latest_event["node"]],
                              [latest_event])
        return None

    def _fire(self, now: float, rule: str, sectors: list[str],
              events: list[dict]) -> dict:
        self._last_alert_ts = now
        return {
            "type": "alert",
            "rule": rule,
            "ts": now,
            "sectors": sectors,
            "contacts": [
                {
                    "sector": e["node"],
                    "yolo_hits": [h["label"] for h in e.get("yolo_hits", [])],
                    "description": e.get("gemini_description"),
                }
                for e in events
            ],
        }
