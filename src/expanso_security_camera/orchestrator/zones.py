"""Cross-zone people tally + threshold flag.

This is the headline of the people-counting demo: two cameras, two zones,
one fusion node that MERGES the per-zone people counts. The alert fires on
the merged reading with a SHAPE requirement — the crowd must SPAN both
cameras, not pile into one. Specifically the FLAG trips when:

    * combined total >= MIN_TOTAL            ("at least three across both")
    * every zone has  >= MIN_PER_ZONE        ("at least one in each camera")
    * the busiest zone >= MIN_PEAK_ZONE      ("two or more in one")

so for the two-camera booth: north>=1 AND south>=1 AND north+south>=3.

    zone north:  2 people     ← spread…
    zone south:  1 person     ← …across both
    ───────────────────────
    combined:    3 / 3        ← FLAG. Both occupied + 3 total.

    zone north:  4 people     ← all in one camera
    zone south:  0 people     ← nobody here
    ───────────────────────
    combined:    4 / 3        ← NO FLAG. Not spread across both zones.

Design:

  * Each sensor emits an event every couple of seconds carrying its current
    `yolo_hits`. The number of "person" hits in the latest event for a zone
    IS that zone's live people count. Empty heartbeat events (yolo_hits=[])
    naturally decay a zone back to 0.

  * `record(event)` updates the latest count + wall-clock for a zone.

  * `snapshot()` recomputes the combined total, applying a staleness window
    so a zone that has gone silent (sensor died, not just "0 people") drops
    out of the total instead of pinning a stale crowd reading on screen.

  * `evaluate_alert()` is EDGE-TRIGGERED: it returns an alert exactly once
    when the combined total transitions from at-or-below the threshold to
    above it, then stays quiet (respecting a cooldown) until the count
    drops back under the threshold and crosses up again. This keeps the
    big red FLAG from re-firing every heartbeat while a crowd lingers.
"""

from __future__ import annotations

import os
import time
from typing import Optional

# Default zones for the two-camera booth demo. Kept here (not hardcoded in
# the orchestrator) so a three-camera deployment is a one-line change.
DEFAULT_ZONES: tuple[str, ...] = ("sensor-north", "sensor-south")

# Crowd-flag rule (see module docstring). The flag requires the crowd to span
# both cameras, not just a raw combined total:
#   MIN_TOTAL     — combined people across all zones to consider flagging
#   MIN_PER_ZONE  — minimum in EACH zone (so a lone cluster in one doesn't flag)
#   MIN_PEAK_ZONE — minimum in the busiest zone
# Defaults encode "at least three across both, one in each, two in one."
# EDGE_CROWD_THRESHOLD is kept as the name for MIN_TOTAL for backward compat.
MIN_TOTAL = int(os.environ.get("EDGE_CROWD_THRESHOLD", "3"))
MIN_PER_ZONE = int(os.environ.get("EDGE_CROWD_MIN_PER_ZONE", "1"))
MIN_PEAK_ZONE = int(os.environ.get("EDGE_CROWD_MIN_PEAK_ZONE", "2"))

# Back-compat alias: ZoneCounter's `threshold` param == MIN_TOTAL.
DEFAULT_THRESHOLD = MIN_TOTAL

# A zone whose last event is older than this is treated as "no reading"
# (sensor stalled) and contributes 0 to the combined total, rather than
# pinning its last count forever. The sensor heartbeat is ~2s, so 6s is
# three missed beats — comfortably "this zone went quiet."
STALE_SEC = float(os.environ.get("EDGE_ZONE_STALE_SEC", "6.0"))

# Re-arm guard so a lingering crowd doesn't re-fire the FLAG every beat.
COOLDOWN_SEC = float(os.environ.get("EDGE_CROWD_COOLDOWN_SEC", "8.0"))


def _person_count(event: dict) -> int:
    return sum(1 for h in event.get("yolo_hits", []) if h.get("label") == "person")


def is_crowd(
    counts: dict[str, int],
    min_total: int = MIN_TOTAL,
    min_per_zone: int = MIN_PER_ZONE,
    min_peak_zone: int = MIN_PEAK_ZONE,
) -> bool:
    """The crowd-flag predicate, shared by the dashboard merge (ZoneCounter)
    and the standalone fuse pipeline so both agree on when to flag.

    Trips only when the crowd SPANS the zones: combined >= min_total AND every
    zone >= min_per_zone AND the busiest zone >= min_peak_zone. For the booth's
    two cameras that's north>=1 AND south>=1 AND north+south>=3.
    """
    vals = list(counts.values())
    total = sum(vals)
    return (
        total >= min_total
        and all(c >= min_per_zone for c in vals)
        and max(vals, default=0) >= min_peak_zone
    )


class ZoneCounter:
    def __init__(
        self,
        zones: tuple[str, ...] = DEFAULT_ZONES,
        threshold: int = DEFAULT_THRESHOLD,
        stale_sec: float = STALE_SEC,
        cooldown_sec: float = COOLDOWN_SEC,
        min_per_zone: int = MIN_PER_ZONE,
        min_peak_zone: int = MIN_PEAK_ZONE,
    ) -> None:
        self.zones = list(zones)
        # `threshold` is the combined-total floor (MIN_TOTAL); the per-zone and
        # peak requirements below shape it so the crowd must span both cameras.
        self.threshold = threshold
        self.min_per_zone = min_per_zone
        self.min_peak_zone = min_peak_zone
        self.stale_sec = stale_sec
        self.cooldown_sec = cooldown_sec
        # zone -> {"count": int, "wall": float}. `wall` is wall-clock at
        # receipt (time.time()), used for staleness — NOT the event ts,
        # which can be replayed/offline and would defeat the decay.
        self._zones: dict[str, dict] = {}
        self._was_over = False
        # -inf so the very FIRST threshold crossing always fires; the
        # cooldown only ever gates re-fires after a real prior alert.
        self._last_alert_wall = float("-inf")

    def record(self, event: dict, now: Optional[float] = None) -> None:
        """Update a zone's live people count from a freshly received event.

        Skips replayed/offline events — a stale archive drain shouldn't
        resurrect an old crowd reading on the live tally.
        """
        node = event.get("node")
        if not node or node == "fusion-node":
            return
        if event.get("queued_offline"):
            return
        wall = time.time() if now is None else now
        self._zones[node] = {"count": _person_count(event), "wall": wall}
        # Track zones we actually see, so a deployment with differently named
        # sensors still tallies without config edits.
        if node not in self.zones:
            self.zones.append(node)

    def _live_counts(self, now: float) -> dict[str, int]:
        """Per-zone count with stale zones zeroed out."""
        counts: dict[str, int] = {}
        for zone in self.zones:
            entry = self._zones.get(zone)
            if entry and (now - entry["wall"]) <= self.stale_sec:
                counts[zone] = int(entry["count"])
            else:
                counts[zone] = 0
        return counts

    def snapshot(self, now: Optional[float] = None) -> dict:
        """Current tally for the dashboard. Always safe to call."""
        now = time.time() if now is None else now
        counts = self._live_counts(now)
        total = sum(counts.values())
        return {
            "type": "zones",
            "counts": counts,
            "zones": [{"zone": z, "count": counts[z]} for z in self.zones],
            "total": total,
            "threshold": self.threshold,
            "over": is_crowd(counts, self.threshold, self.min_per_zone, self.min_peak_zone),
        }

    def evaluate_alert(self, now: Optional[float] = None) -> Optional[dict]:
        """Edge-triggered crowd alert. Returns an alert dict the first time
        the combined total crosses above the threshold, then stays quiet
        until it drops back under and crosses again (cooldown-guarded).
        """
        now = time.time() if now is None else now
        snap = self.snapshot(now)
        total = snap["total"]
        over = snap["over"]

        alert: Optional[dict] = None
        if over and not self._was_over and (now - self._last_alert_wall) >= self.cooldown_sec:
            self._last_alert_wall = now
            counts = snap["counts"]
            alert = {
                "type": "alert",
                "rule": "crowd_threshold",
                "ts": now,
                "total": total,
                "threshold": self.threshold,
                "sectors": [z for z, c in counts.items() if c > 0] or self.zones,
                "contacts": [
                    {
                        "sector": z,
                        "count": counts.get(z, 0),
                        "yolo_hits": ["person"] * counts.get(z, 0),
                        "description": f"{counts.get(z, 0)} person(s) in zone",
                    }
                    for z in self.zones
                ],
            }
        # Latch on the EDGE: only reset once we drop back to/under threshold,
        # so a crowd hovering at total==threshold+1 doesn't re-alert each beat.
        self._was_over = over
        return alert
