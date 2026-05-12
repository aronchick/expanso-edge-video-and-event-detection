#!/usr/bin/env python3
"""Cross-sector fusion correlator.

Tails the orchestrator's events.ndjson, maintains a 5-second sliding
window of (sector, class) → last-seen-event, and emits a JSON fused
alert to stdout whenever both sectors see the same triggered class
within the window. 8-second cooldown per class after each fire
prevents alert storms when a person is stationary in both FOVs.

Used as the `subprocess` input of the `fuse` Expanso pipeline
(jobs/fuse-job.yaml). The pipeline then enriches each fused alert
with archive lineage and a fuse-sha256 signature, fanning out to
stdout (cluster log), file, and HTTP POST to the orchestrator.

Runs at the edge — same node as the sensors and the orchestrator.
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

TRIGGERED_CLASSES = {"person", "backpack", "drone"}
WINDOW_SEC = 5.0
COOLDOWN_SEC = 8.0


def tail(path: Path):
    """Generator yielding new lines as they're appended to `path`."""
    # If the file doesn't exist yet, wait for it.
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


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: fuse-correlator <events.ndjson path>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])

    # last_seen[sector][class] = timestamp of latest event for that pair
    last_seen: dict[str, dict[str, float]] = defaultdict(dict)
    # cooldown_until[class] = no-fire-before timestamp
    cooldown_until: dict[str, float] = {}

    # No stdout/stderr chatter at startup — Bento's subprocess input
    # treats any line we emit (stderr included on some platforms) as a
    # message and logs a parse error if it isn't JSON. Stay silent until
    # we actually have a fused alert to emit.

    for raw in tail(path):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Filter: skip non-sensor events (orchestrator-emitted fused
        # alerts, anything from fusion-node, etc.). We only correlate
        # raw sensor outputs.
        node = event.get("node", "")
        if not node.startswith("sensor-"):
            continue
        ts = event.get("ts")
        if ts is None:
            continue

        labels = {h.get("label") for h in event.get("yolo_hits", [])}
        triggered = labels & TRIGGERED_CLASSES
        if not triggered:
            continue

        for cls in triggered:
            last_seen[node][cls] = ts
            # Look at every OTHER sector that has this class within window
            for other_node, other_classes in last_seen.items():
                if other_node == node:
                    continue
                other_ts = other_classes.get(cls)
                if other_ts is None:
                    continue
                dt = ts - other_ts
                if dt <= 0 or dt >= WINDOW_SEC:
                    continue
                # Cooldown check
                if cls in cooldown_until and ts < cooldown_until[cls]:
                    continue
                # Fire fused alert
                alert = {
                    "type": "multi_sector_correlation",
                    "fused_alert": True,
                    "class": cls,
                    "sectors": sorted([node, other_node]),
                    "first_seen_at": other_ts,
                    "fused_at": ts,
                    "delta_sec": round(dt, 3),
                    "source_event_signature": event.get("signature", ""),
                }
                print(json.dumps(alert), flush=True)
                cooldown_until[cls] = ts + COOLDOWN_SEC

    return 0


if __name__ == "__main__":
    sys.exit(main())
