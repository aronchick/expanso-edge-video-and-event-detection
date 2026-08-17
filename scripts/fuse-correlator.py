#!/usr/bin/env python3
"""Cross-zone people-count merge — the headline correlation, as a pipeline.

Tails the orchestrator's events.ndjson and MERGES the per-zone people
counts across both cameras. Emits a JSON crowd alert to stdout when the
merged reading SPANS both cameras: combined >= 3 AND every zone >= 1 AND
the busiest zone >= 2 — not when any single zone piles up alone. This is
the same logic as the in-orchestrator ZoneCounter (orchestrator/zones.py),
expressed here as a standalone streaming correlator so the merge runs as
its OWN observable Expanso Edge pipeline (`fuse`) visible in
cloud.expanso.io alongside sensor-north / sensor-south / fusion-node.

Used as the `subprocess` input of the `fuse` Expanso pipeline
(jobs/fuse-job.yaml). The pipeline enriches each crowd alert with archive
lineage + a fuse-sha256 signature and fans out to stdout (cluster log)
and a local ndjson audit file.

  zone north: 2 people   ← spread…
  zone south: 1 person   ← …across both
  ─────────────────────
  combined:   3 / 3      ← FLAG. Both occupied + 3 total.

  zone north: 4 people   ← all in one camera
  zone south: 0 people   ← nobody here
  ─────────────────────
  combined:   4 / 3      ← NO FLAG. Not spread across both zones.

Runs at the edge — same node as the sensors and the orchestrator.
Threshold via EDGE_CROWD_THRESHOLD; staleness via EDGE_ZONE_STALE_SEC;
cooldown via EDGE_CROWD_COOLDOWN_SEC (kept in sync with zones.py).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Crowd-flag rule — kept in lockstep with orchestrator/zones.py:is_crowd.
# Flags only when the crowd SPANS both cameras: combined >= MIN_TOTAL AND every
# zone >= MIN_PER_ZONE AND the busiest zone >= MIN_PEAK_ZONE. For the two
# cameras: north>=1 AND south>=1 AND north+south>=3.
MIN_TOTAL = int(os.environ.get("EDGE_CROWD_THRESHOLD", "3"))
MIN_PER_ZONE = int(os.environ.get("EDGE_CROWD_MIN_PER_ZONE", "1"))
MIN_PEAK_ZONE = int(os.environ.get("EDGE_CROWD_MIN_PEAK_ZONE", "2"))
STALE_SEC = float(os.environ.get("EDGE_ZONE_STALE_SEC", "6.0"))
COOLDOWN_SEC = float(os.environ.get("EDGE_CROWD_COOLDOWN_SEC", "8.0"))


def tail(path: Path):
    """Generator yielding new lines as they're appended to `path`."""
    while not path.exists():
        time.sleep(0.5)
    with path.open("r") as f:
        # Start at end — we only care about new events.
        f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue
            yield line.rstrip("\n")


def _person_count(event: dict) -> int:
    return sum(1 for h in event.get("yolo_hits", []) if h.get("label") == "person")


def _is_crowd(counts: dict[str, int]) -> bool:
    """Crowd-flag predicate — mirrors orchestrator/zones.py:is_crowd."""
    vals = list(counts.values())
    return (
        sum(vals) >= MIN_TOTAL
        and all(c >= MIN_PER_ZONE for c in vals)
        and max(vals, default=0) >= MIN_PEAK_ZONE
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: fuse-correlator <events.ndjson path>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])

    # zone -> {"count": int, "wall": float}. `wall` is wall-clock at receipt
    # (used for staleness — NOT the event ts, which can be replayed).
    zones: dict[str, dict] = {}
    was_over = False
    last_alert_wall = float("-inf")

    # No stdout/stderr chatter at startup — Bento's subprocess input treats
    # any non-JSON line we emit as a message and logs a parse error. Stay
    # silent until we actually have a crowd alert to emit.

    for raw in tail(path):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Only merge raw sensor outputs — skip fusion-node alerts, replays.
        node = event.get("node", "")
        if not node.startswith("sensor-"):
            continue
        if event.get("queued_offline"):
            continue

        now = time.time()
        zones[node] = {"count": _person_count(event), "wall": now}

        # Combined total across all fresh zones (stale zones drop out).
        counts = {
            z: (e["count"] if (now - e["wall"]) <= STALE_SEC else 0) for z, e in zones.items()
        }
        total = sum(counts.values())
        over = _is_crowd(counts)

        # Edge-triggered: fire once when the combined total crosses above the
        # threshold, then stay quiet until it drops back under and re-crosses
        # (cooldown-guarded). Mirrors ZoneCounter.evaluate_alert.
        if over and not was_over and (now - last_alert_wall) >= COOLDOWN_SEC:
            last_alert_wall = now
            alert = {
                "type": "crowd_threshold",
                "fused_alert": True,
                "rule": "crowd_threshold",
                "total": total,
                "threshold": MIN_TOTAL,
                "counts": dict(counts),
                "sectors": sorted(z for z, c in counts.items() if c > 0),
                "fused_at": now,
            }
            print(json.dumps(alert), flush=True)
        was_over = over

    return 0


if __name__ == "__main__":
    sys.exit(main())
