"""Simulate the inference pipeline for dashboard testing.

Generates realistic crossing events without needing cameras or YOLO.
Useful for testing the dashboard and Expanso pipeline on any machine.

Run with:
    python src/simulate.py [--discrepancy]

Flags:
    --discrepancy   Simulate a 2-box discrepancy (12 departed, 10 arrived)
    --fast          Speed up simulation (0.5s between events)
    --people        Simulate people counting instead of boxes
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from expanso_security_camera.events import (
    BoundingBox,
    Centroid,
    CrossingEvent,
    DashboardState,
    DetectionInfo,
    Direction,
    FrameInfo,
    ModelInfo,
    emit_event,
    write_state,
)


def log(msg: str) -> None:
    """Log to stderr so stdout stays clean for Expanso."""
    print(msg, file=sys.stderr, flush=True)


STATE_PATH = "state.json"
EVENTS_PATH = "raw-events.ndjson"
COMMANDS_PATH = "commands.json"


def simulate(
    total_boxes: int = 12,
    discrepancy: int = 0,
    delay: float = 2.0,
    mode: str = "box",
) -> None:
    """Simulate a box/person transfer demo."""
    item_name = "box" if mode == "box" else "person"
    log(f"\n{'=' * 60}")
    log(f"  Simulating {item_name} transfer demo")
    log(f"  Total items: {total_boxes}")
    log(f"  Expected discrepancy: {discrepancy}")
    log(f"  Delay between events: {delay}s")
    log(f"{'=' * 60}\n")

    # Clear state
    Path(EVENTS_PATH).write_text("")
    state = DashboardState(detect_mode=mode)
    write_state(STATE_PATH, state)

    outside_count = 0
    inside_count = 0
    # Which boxes to "drop" (not deliver to inside)
    dropped = (
        set(random.sample(range(1, total_boxes + 1), discrepancy)) if discrepancy > 0 else set()
    )

    if dropped:
        log(f"  Will drop items #{sorted(dropped)} (discrepancy simulation)\n")

    frame_num = 0

    for box_num in range(1, total_boxes + 1):
        # Check for reset command
        if Path(COMMANDS_PATH).exists():
            try:
                cmd = json.loads(Path(COMMANDS_PATH).read_text())
                if cmd.get("action") == "reset":
                    log("\n>>> SESSION RESET <<<\n")
                    outside_count = 0
                    inside_count = 0
                    state = DashboardState(detect_mode=mode)
                    write_state(STATE_PATH, state)
                    Path(EVENTS_PATH).write_text("")
                os.remove(COMMANDS_PATH)
            except (json.JSONDecodeError, OSError):
                pass

        frame_num += random.randint(30, 60)
        now = datetime.now(timezone.utc)

        # --- Outside camera: box departs ---
        outside_count += 1
        conf = round(random.uniform(0.55, 0.92), 2)
        x1 = random.randint(200, 400)
        y1 = random.randint(200, 300)
        x2 = x1 + random.randint(80, 150)
        y2 = y1 + random.randint(100, 180)

        event_outside = CrossingEvent(
            camera_id="cam-outside",
            detection=DetectionInfo(
                track_id=box_num,
                object_class=item_name,
                object_class_raw="suitcase" if mode == "box" else "person",
                confidence=conf,
                bounding_box=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                centroid=Centroid(x=(x1 + x2) / 2, y=(y1 + y2) / 2),
                direction=Direction.OUTBOUND,
                crossing_line_id="cam-outside-line-1",
            ),
            model=ModelInfo(inference_time_ms=round(random.uniform(22, 35), 1)),
            frame=FrameInfo(
                frame_number=frame_num,
                frame_timestamp=now.isoformat(),
            ),
            counts={
                "camera_departures": outside_count,
                "camera_arrivals": 0,
            },
        )
        emit_event(event_outside)

        now_str = now.strftime("%I:%M:%S %p")
        log(
            f"  {now_str}  cam-outside  {item_name.capitalize()} "
            f"#{box_num} departed  (count: {outside_count})"
        )

        # Update state after departure
        state.camera_outside_departures = outside_count
        state.last_event_ts = now.isoformat()
        state.inference_fps = round(random.uniform(15.5, 17.8), 1)

        # Add to event log
        state.recent_events.append(
            {
                "camera_id": "cam-outside",
                "departures": outside_count,
                "arrivals": 0,
                "object_class_raw": "suitcase" if mode == "box" else "person",
                "confidence": conf,
                "frame_timestamp": now.isoformat(),
            }
        )

        write_state(STATE_PATH, state)
        time.sleep(delay * 0.4)

        # --- Inside camera: box arrives (unless dropped) ---
        if box_num not in dropped:
            inside_count += 1
            frame_num += random.randint(10, 30)
            now = datetime.now(timezone.utc)
            conf = round(random.uniform(0.50, 0.88), 2)

            event_inside = CrossingEvent(
                camera_id="cam-inside",
                detection=DetectionInfo(
                    track_id=box_num + 100,
                    object_class=item_name,
                    object_class_raw="suitcase" if mode == "box" else "person",
                    confidence=conf,
                    bounding_box=BoundingBox(x1=x1 + 50, y1=y1, x2=x2 + 50, y2=y2),
                    centroid=Centroid(x=(x1 + x2) / 2 + 50, y=(y1 + y2) / 2),
                    direction=Direction.INBOUND,
                    crossing_line_id="cam-inside-line-1",
                ),
                model=ModelInfo(inference_time_ms=round(random.uniform(22, 35), 1)),
                frame=FrameInfo(
                    frame_number=frame_num,
                    frame_timestamp=now.isoformat(),
                ),
                counts={
                    "camera_departures": 0,
                    "camera_arrivals": inside_count,
                },
            )
            emit_event(event_inside)

            now_str = now.strftime("%I:%M:%S %p")
            log(
                f"  {now_str}  cam-inside   {item_name.capitalize()} "
                f"#{box_num} arrived   (count: {inside_count})"
            )

            state.recent_events.append(
                {
                    "camera_id": "cam-inside",
                    "departures": 0,
                    "arrivals": inside_count,
                    "object_class_raw": "suitcase" if mode == "box" else "person",
                    "confidence": conf,
                    "frame_timestamp": now.isoformat(),
                }
            )
        else:
            now_str = datetime.now().strftime("%I:%M:%S %p")
            log(
                f"  {now_str}  -----        {item_name.capitalize()} "
                f"#{box_num} DROPPED  (not delivered)"
            )

        # Update reconciliation
        state.camera_inside_arrivals = inside_count
        disc = outside_count - inside_count
        state.discrepancy = disc
        state.status = "MATCH" if disc == 0 else "DISCREPANCY"
        state.last_event_ts = datetime.now(timezone.utc).isoformat()

        if disc > 0 and state.first_discrepancy_at is None:
            state.first_discrepancy_at = datetime.now(timezone.utc).isoformat()

        state.recent_events = state.recent_events[-50:]
        write_state(STATE_PATH, state)

        time.sleep(delay * 0.6)

    # Final summary
    log(f"\n{'=' * 60}")
    log("  Transfer Complete")
    log(f"  Outside departures: {outside_count}")
    log(f"  Inside arrivals:    {inside_count}")
    if outside_count == inside_count:
        log(f"  Status: ✅ ALL {item_name.upper()}S ACCOUNTED FOR")
    else:
        log(f"  Status: ⚠️  {outside_count - inside_count} {item_name.upper()}S UNACCOUNTED FOR")
    log(f"{'=' * 60}\n")

    # Keep running so dashboard can display final state
    log("Simulation complete. Dashboard will show final state.")
    log("Press Ctrl+C to exit.\n")
    try:
        while True:
            # Check for reset
            if Path(COMMANDS_PATH).exists():
                try:
                    cmd = json.loads(Path(COMMANDS_PATH).read_text())
                    if cmd.get("action") == "reset":
                        log("Reset received. Restarting simulation...")
                        os.remove(COMMANDS_PATH)
                        simulate(total_boxes, discrepancy, delay, mode)
                        return
                except (json.JSONDecodeError, OSError):
                    pass
            time.sleep(1)
    except KeyboardInterrupt:
        log("\nDone.")


def main() -> None:
    """Entry point for esc-simulate command."""
    args = set(sys.argv[1:])
    disc = 2 if "--discrepancy" in args else 0
    delay = 0.5 if "--fast" in args else 2.0
    mode = "person" if "--people" in args else "box"
    simulate(total_boxes=12, discrepancy=disc, delay=delay, mode=mode)


if __name__ == "__main__":
    main()
