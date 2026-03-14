# Security Camera Box Counting Demo — MVP PRD

## Version: 1.0.0
## Date: March 2026
## Status: MVP Scope Locked (Hardware Confirmed, Expanso-First Architecture)

---

## Executive Summary

A 48-hour MVP demo for a warehouse logistics customer (Array/Wall-E). Two 4K PoE cameras on a Jetson Orin Nano edge box count boxes moving through a dock door and flag discrepancies in real-time. The entire pipeline — ingest, inference, tracking, counting, event enrichment, buffering, and upstream delivery — is orchestrated by Expanso Edge.

**This demo proves two things:**
1. Automated reconciliation of box transfers catches discrepancies that manual observation misses — on an $866 system with zero cloud dependency for inference
2. **Expanso is the production edge platform** that transforms raw detections into structured, schema-rich, mergeable business events and delivers them reliably from edge to destination

The detection is commodity (open-source YOLO). **Expanso is the value layer** — it turns bounding boxes into auditable chain-of-custody records that survive network outages, merge across cameras, and integrate downstream without custom plumbing.

---

## Problem Statement

Warehouse operations move goods between locations (truck → dock, dock → staging area) with no automated verification that all items arrive.

**Current state at the customer site:**
- One portable camera manually positioned by a shift lead or worker at start of shift
- Camera provides passive video recording with no analytics
- Hourly workers unload trucks with no on-site manager present
- Workers leave when the truck is finished — if they take long breaks, trucks still get done, just slower
- Nobody can verify item counts until a manual inventory audit

**Specific problems this demo addresses:**
1. **No automated counting** — someone must review hours of footage to verify counts
2. **No discrepancy detection** — if 2 out of 12 boxes disappear between origin and destination, nobody knows until audit
3. **The people setting up the camera are the ones being monitored** — misalignment of incentives
4. **Camera positioning is error-prone** — this demo includes a visual alignment check confirming the camera sees the counting zone correctly before starting (full auto-tracking is v5)

---

## Target Users

### Primary: Remote Warehouse Manager (Joe)
- Manages multiple warehouse sites without being physically present
- Wants to see: "12 boxes left the truck, 12 arrived at staging. All accounted for."
- Or: "12 left, 10 arrived. 2 missing. Investigate."

### Secondary: Dock Workers
- Set up camera at start of shift
- Want an automated record proving items were handled correctly

---

## MVP User Stories

### US-1: Clean Transfer (Demo 1 — "Dock Door")
**As** a warehouse manager, **I want** to see that all boxes moved through a dock door were counted by cameras on both sides, **so that** I can verify a complete transfer without being on-site.

**Acceptance criteria:**
- Camera 1 (outside) watches the staging area / truck side
- Camera 2 (inside) watches the dock interior / receiving area
- Person carries 12 boxes through the doorway one at a time
- Dashboard shows Camera 1 departures: 12, Camera 2 arrivals: 12
- Dashboard shows "Counts Match ✅"
- All count events flow through Expanso with full metadata
- Demo takes ~3 minutes to record

### US-2: Discrepancy Detection (Demo 1b — "The Missing Boxes")
**As** a warehouse manager, **I want** to be alerted when the receiving count doesn't match the departure count, **so that** I can investigate missing items immediately.

**Acceptance criteria:**
- Same two-camera setup, new session started
- Person carries 12 boxes toward the door, but sets aside 2 before they reach Camera 2's view
- Camera 1 departures: 12, Camera 2 arrivals: 10
- Dashboard shows "⚠️ DISCREPANCY: 2 boxes unaccounted for"
- Discrepancy event generated through Expanso with full context (which camera, when, session ID)
- Alert is timestamped

### US-3: People Counting (Demo 2 — "General Purpose")
**As** a building manager, **I want** to count people entering/exiting through two entrances, **so that** I can track occupancy.

**Acceptance criteria:**
- Same hardware, config changed to `detect_classes: ["person"]`
- Cameras at two entrances counting people in/out
- Dashboard shows per-entrance counts
- Demonstrates the system is configurable — same pipeline, different detection targets

### US-4: Live Dashboard
**As** a warehouse manager, **I want** a real-time dashboard showing transfer status, **so that** I can monitor operations.

**Acceptance criteria:**
- Web-accessible dashboard (Streamlit on the Jetson, accessible from any browser on the network)
- Updates within 5 seconds of a box crossing a counting line
- Shows: Camera 1 count, Camera 2 count, discrepancy status, event log with timestamps
- Reconciliation view: "Outside: 12, Inside: 10, Missing: 2, First detected: 2:47 PM"

---

## Hardware — Confirmed and Purchased

| Item | Model | Cost | Notes |
|------|-------|------|-------|
| Edge compute | Seeed reComputer J3011 — Jetson Orin Nano 8GB, 1024 CUDA cores, 32 Tensor Cores, 67 TOPS (Super Mode), 128GB NVMe, JetPack 6.2 | $716 | Palm-sized AI box. No server rack needed. |
| Cameras (x2) | Reolink RLC-810A — 4K/8MP PoE bullet cameras, RTSP H.264/H.265, 100ft IR night vision | ~$110 | Native RTSP. Onboard human/vehicle detection ignored — we run our own models. |
| PoE switch | TP-Link TL-SG1005P — 5-port gigabit, 4 PoE+ @ 65W, fanless | ~$40 | Powers cameras + Jetson via single switch. |
| **Total** | | **~$866** | Complete system. No NVR, no server, no cloud, no per-camera licensing. |

### RTSP URLs (Reolink RLC-810A)
```
Main stream (4K):  rtsp://<ip>:554/h264Preview_01_main
Sub stream (720p): rtsp://<ip>:554/h264Preview_01_sub
```
Use the **sub stream** for inference (720p is sufficient — YOLO resizes to 640x640 anyway). Save the main stream for evidence recording if needed later.

---

## Architecture — Expanso-First

### Design Principle

**Expanso is not a sidecar.** It is the pipeline. The inference script produces raw detection events as JSON. Everything after that — schema validation, enrichment, windowed aggregation, reconciliation, buffering, upstream delivery — is an Expanso pipeline.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SEEED RECOMPUTER J3011                          │
│                     (Jetson Orin Nano 8GB)                          │
│                                                                     │
│  Reolink Cam 1 ──RTSP──▶ ┌─────────────────────────┐              │
│  (outside)                │  INFERENCE PROCESS       │              │
│  Reolink Cam 2 ──RTSP──▶ │  PyAV decode → YOLOv8s  │              │
│  (inside)                 │  TensorRT FP16 → ByteTrack              │
│                           │  → Line-crossing counter │              │
│                           └──────────┬──────────────┘              │
│                                      │ Raw detection events (JSON) │
│                                      │ written to NDJSON file      │
│                                      ▼                              │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    EXPANSO EDGE PIPELINE                      │  │
│  │                                                               │  │
│  │  INPUT: file (watch raw-events.ndjson)                        │  │
│  │    │                                                          │  │
│  │    ▼                                                          │  │
│  │  PROCESSOR 1: Schema Validation                               │  │
│  │    Validate against CountEvent / CrossingEvent schema          │  │
│  │    Reject malformed events, log errors                        │  │
│  │    │                                                          │  │
│  │    ▼                                                          │  │
│  │  PROCESSOR 2: Metadata Enrichment                             │  │
│  │    Add: device_id, site_id, session_id, model_version,       │  │
│  │    camera_config (position, FOV, counting_line),              │  │
│  │    environmental context (lighting, weather from sensor)       │  │
│  │    │                                                          │  │
│  │    ▼                                                          │  │
│  │  PROCESSOR 3: Windowed Aggregation                            │  │
│  │    Aggregate per-crossing events into time-windowed counts     │  │
│  │    Default window: 30 seconds                                 │  │
│  │    │                                                          │  │
│  │    ▼                                                          │  │
│  │  PROCESSOR 4: Cross-Camera Reconciliation                     │  │
│  │    Compare camera_outside.departures vs camera_inside.arrivals│  │
│  │    Generate DiscrepancyEvent if delta != 0                    │  │
│  │    │                                                          │  │
│  │    ▼                                                          │  │
│  │  BUFFER: Memory + Disk (NVMe)                                 │  │
│  │    Survives network outages. Flushes when connectivity returns.│  │
│  │    │                                                          │  │
│  │    ├──▶ OUTPUT 1: Local Dashboard (HTTP/WebSocket to Streamlit)│  │
│  │    ├──▶ OUTPUT 2: Local NDJSON archive (on NVMe SSD)          │  │
│  │    └──▶ OUTPUT 3: Upstream HTTP POST (to Expanso Cloud / API) │  │
│  │                                                               │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  STREAMLIT DASHBOARD (localhost:8501, accessible on LAN)      │  │
│  │  Reads enriched events from Expanso output                    │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                     LTE / WiFi / Ethernet
                              │
                              ▼
               ┌──────────────────────────┐
               │   EXPANSO CLOUD          │
               │   (or upstream API)      │
               │   Receives enriched,     │
               │   validated, mergeable   │
               │   count events           │
               └──────────────────────────┘
```

### Why This Architecture

**The inference process is intentionally dumb.** It reads RTSP frames, runs YOLO, runs ByteTrack, detects line crossings, and emits raw JSON events to a file. That's it. No aggregation, no reconciliation, no networking, no buffering, no schema management. It's a single-purpose detection engine that's easy to test and replace.

**Expanso does everything after detection.** This is the demo's thesis: you can swap the detection engine (YOLO today, custom model tomorrow, different sensor entirely next year) and Expanso handles all the hard parts — validation, enrichment, aggregation, reconciliation, delivery, and offline resilience. The pipeline config is declarative YAML. Adding a new downstream consumer is adding 5 lines of output config, not writing code.

---

## Event Schema — Rich, Mergeable, Downstream-Ready

### Design Goals for the Schema

1. **Self-describing:** Every event carries enough context to be understood in isolation, without needing to look up the device, camera, or session
2. **Mergeable:** Events from multiple cameras / devices / sites can be combined downstream by matching on `session_id` + `site_id` + time windows
3. **Versioned:** Schema includes a version field so downstream consumers can handle evolution
4. **Auditable:** Every event traces back to: which device, which camera, which model, what confidence threshold, what time window

### Event Types

#### 1. CrossingEvent (per-object, real-time)

Emitted by the inference process every time a tracked object crosses a counting line.

```json
{
  "schema_version": "1.0.0",
  "event_type": "crossing",
  "event_id": "evt_a1b2c3d4",
  "timestamp": "2026-03-15T14:47:23.456Z",

  "source": {
    "device_id": "recomputer-j3011-001",
    "site_id": "warehouse-alpha",
    "camera_id": "cam-outside",
    "camera_position": "dock-door-exterior",
    "stream_url": "rtsp://192.168.1.100:554/h264Preview_01_sub"
  },

  "session": {
    "session_id": "sess_20260315_shift2",
    "session_start": "2026-03-15T14:00:00Z",
    "operator_id": "shift-lead-badge-4521"
  },

  "detection": {
    "track_id": 7,
    "object_class": "box",
    "object_class_raw": "suitcase",
    "confidence": 0.72,
    "bounding_box": {"x1": 234, "y1": 156, "x2": 389, "y2": 312},
    "centroid": {"x": 311, "y": 234},
    "direction": "outbound",
    "crossing_line_id": "dock-door-line-1"
  },

  "model": {
    "model_name": "yolov8s",
    "model_version": "8.1.0",
    "precision": "fp16",
    "engine": "tensorrt",
    "input_resolution": "640x640",
    "inference_time_ms": 28.3
  },

  "counts": {
    "camera_total_departures": 7,
    "camera_total_arrivals": 0,
    "session_running_count": 7
  },

  "frame": {
    "frame_number": 4521,
    "frame_timestamp": "2026-03-15T14:47:23.412Z",
    "resolution": "1280x720"
  }
}
```

#### 2. WindowedCountEvent (aggregated, periodic)

Generated by Expanso's aggregation processor. Summarizes counts per camera per time window.

```json
{
  "schema_version": "1.0.0",
  "event_type": "windowed_count",
  "event_id": "wnd_e5f6g7h8",
  "timestamp": "2026-03-15T14:47:30Z",

  "source": {
    "device_id": "recomputer-j3011-001",
    "site_id": "warehouse-alpha",
    "camera_id": "cam-outside"
  },

  "session": {
    "session_id": "sess_20260315_shift2"
  },

  "window": {
    "window_start": "2026-03-15T14:47:00Z",
    "window_end": "2026-03-15T14:47:30Z",
    "window_duration_seconds": 30
  },

  "counts": {
    "departures": 4,
    "arrivals": 0,
    "unique_tracks": 4,
    "by_class": {
      "box": {"departures": 4, "arrivals": 0}
    }
  },

  "session_totals": {
    "total_departures": 7,
    "total_arrivals": 0
  },

  "quality": {
    "avg_confidence": 0.68,
    "min_confidence": 0.41,
    "dropped_frames": 0,
    "inference_fps": 16.8
  }
}
```

#### 3. ReconciliationEvent (cross-camera, periodic)

Generated by Expanso's reconciliation processor. Merges counts from both cameras.

```json
{
  "schema_version": "1.0.0",
  "event_type": "reconciliation",
  "event_id": "rec_i9j0k1l2",
  "timestamp": "2026-03-15T14:47:30Z",

  "source": {
    "device_id": "recomputer-j3011-001",
    "site_id": "warehouse-alpha"
  },

  "session": {
    "session_id": "sess_20260315_shift2"
  },

  "window": {
    "window_start": "2026-03-15T14:47:00Z",
    "window_end": "2026-03-15T14:47:30Z"
  },

  "reconciliation": {
    "camera_outside": {
      "camera_id": "cam-outside",
      "departures": 7,
      "arrivals": 0
    },
    "camera_inside": {
      "camera_id": "cam-inside",
      "departures": 0,
      "arrivals": 5
    },
    "expected": 7,
    "received": 5,
    "discrepancy": 2,
    "status": "DISCREPANCY",
    "first_discrepancy_at": "2026-03-15T14:46:45Z"
  },

  "session_totals": {
    "outside_departures": 7,
    "inside_arrivals": 5,
    "cumulative_discrepancy": 2
  }
}
```

#### 4. DiscrepancyAlert (triggered, actionable)

Generated by Expanso when reconciliation detects a non-zero discrepancy.

```json
{
  "schema_version": "1.0.0",
  "event_type": "discrepancy_alert",
  "event_id": "alert_m3n4o5p6",
  "timestamp": "2026-03-15T14:47:30Z",
  "severity": "warning",

  "source": {
    "device_id": "recomputer-j3011-001",
    "site_id": "warehouse-alpha"
  },

  "session": {
    "session_id": "sess_20260315_shift2",
    "operator_id": "shift-lead-badge-4521"
  },

  "alert": {
    "message": "2 boxes departed but did not arrive",
    "outside_count": 7,
    "inside_count": 5,
    "missing_count": 2,
    "first_detected": "2026-03-15T14:46:45Z",
    "duration_seconds": 45,
    "reconciliation_event_id": "rec_i9j0k1l2"
  }
}
```

#### 5. DeviceHealthEvent (periodic, operational)

Generated by Expanso on a schedule (every 60 seconds).

```json
{
  "schema_version": "1.0.0",
  "event_type": "device_health",
  "event_id": "hlth_q7r8s9t0",
  "timestamp": "2026-03-15T14:47:00Z",

  "source": {
    "device_id": "recomputer-j3011-001",
    "site_id": "warehouse-alpha"
  },

  "device": {
    "model": "seeed-recomputer-j3011",
    "jetpack_version": "6.2",
    "uptime_seconds": 86400,
    "gpu_utilization_pct": 72,
    "gpu_temperature_c": 54,
    "cpu_utilization_pct": 35,
    "memory_used_mb": 4200,
    "memory_total_mb": 8192,
    "nvme_used_gb": 12,
    "nvme_total_gb": 128
  },

  "streams": [
    {
      "camera_id": "cam-outside",
      "status": "connected",
      "fps_actual": 15.2,
      "fps_target": 15,
      "decode_lag_ms": 12,
      "last_frame_at": "2026-03-15T14:46:59Z"
    },
    {
      "camera_id": "cam-inside",
      "status": "connected",
      "fps_actual": 14.8,
      "fps_target": 15,
      "decode_lag_ms": 14,
      "last_frame_at": "2026-03-15T14:46:59Z"
    }
  ],

  "pipeline": {
    "inference_fps": 16.8,
    "inference_latency_ms": 28,
    "tracker_active_tracks": 3,
    "events_buffered": 0,
    "events_delivered": 847,
    "events_failed": 0
  }
}
```

### Schema Design Decisions

**Why `object_class` AND `object_class_raw`?** The raw COCO class ("suitcase") is preserved for debugging and model improvement. The mapped class ("box") is what the business logic uses. This mapping is configured in the Expanso pipeline, not in the inference code — so remapping classes for different use cases is a pipeline config change, not a code change.

**Why `session_id`?** Sessions group events into logical work units (one truck unload, one shift). This enables:
- Reconciliation scoped to a session (not mixing counts from different trucks)
- Historical analysis per session
- Merging events from multiple cameras that share a session

**Why inference metadata on every event?** When investigating a miscount, you need to know: what model was running, at what confidence threshold, at what resolution. Attaching this to every event (not just to a session header) means every event is self-contained and can be analyzed without joining to other data sources.

**Why `frame_number` and `frame_timestamp`?** If a discrepancy is flagged, the operator can request the specific frame for visual verification. The frame number + camera ID uniquely identifies which frame to pull from the on-device recording (if frame archiving is enabled in v2).

### Mergeability

Events from multiple devices / sites merge naturally on:
- **Same session:** `session_id` — events from cam-outside and cam-inside merge into one reconciliation view
- **Same site, multiple sessions:** `site_id` + time range — daily summary across all shifts
- **Fleet-wide:** `event_type: "reconciliation"` across all `site_id` values — which sites had discrepancies today?

Downstream consumers can query:
```sql
-- Which sites had discrepancies today?
SELECT site_id, SUM(discrepancy) as total_missing
FROM reconciliation_events
WHERE DATE(timestamp) = '2026-03-15'
  AND status = 'DISCREPANCY'
GROUP BY site_id
ORDER BY total_missing DESC
```

---

## Functional Requirements — MVP

### FR-1: RTSP Camera Ingest
- Process two Reolink RLC-810A RTSP streams simultaneously
- Use sub-stream (720p) for inference, not main stream (4K)
- Each camera in its own thread, frames into thread-safe queue (maxsize=5, drop oldest if full)
- Auto-reconnect on stream failure (exponential backoff, max 60s)
- Log stream health metrics (FPS, decode lag, reconnections)

### FR-2: Object Detection
- YOLOv8s on TensorRT FP16
- Batched inference across both streams for GPU efficiency
- Class-agnostic mode for boxes (detect any object above confidence threshold in counting zone)
- Display label: map raw COCO class to "box" in the pipeline (Expanso handles this mapping)
- For Demo 2 (people counting): `detect_classes: [0]` (COCO person class)

**Detection validation (do FIRST):**
- Point camera at stack of boxes
- Run inference, check detection confidence
- **Pass:** Boxes detected at ≥0.3 confidence with ≤1 false positive per frame
- **Fail:** Adjust confidence threshold, box size, or add high-contrast tape markers

### FR-3: Object Tracking (ByteTrack)
- ByteTrack for persistent track IDs per camera
- Handles brief occlusion (person's body blocks box for a few frames)
- Track IDs included in all CrossingEvents for de-duplication

### FR-4: Line-Crossing Counter with Directional Filtering
- Each camera has a counting line (hardcoded pixel coordinates)
- **Directional filtering:** Camera 1 (outside) only counts rightward/outbound movement. Camera 2 (inside) only counts leftward/inbound movement. Opposite-direction movement is discarded.
- **Size filtering:** Filter by bounding box area to distinguish "person carrying box" from "empty-handed person." Calibrated during setup.
- **State machine per camera:** IDLE → ENTERING → IN_ZONE → CROSSING → COUNTED → COOLDOWN (3s)
- Each crossing emits a raw CrossingEvent to `raw-events.ndjson`

### FR-5: Expanso Edge Pipeline
**This is the core of the demo. Not optional. Not v2. MVP.**

- **Input:** Watch `raw-events.ndjson` for new crossing events from the inference process
- **Schema validation:** Reject malformed events, log errors
- **Metadata enrichment:** Add device_id, site_id, session_id, model metadata, camera config
- **Class mapping:** Map raw COCO classes to business classes (e.g., "suitcase" → "box")
- **Windowed aggregation:** Aggregate crossing events into 30-second count windows
- **Cross-camera reconciliation:** Compare outside departures vs inside arrivals, generate ReconciliationEvent
- **Discrepancy alerting:** Generate DiscrepancyAlert when reconciliation shows non-zero delta
- **Buffering:** Memory + disk buffer on NVMe. Survives network outage. Flushes when connectivity returns.
- **Outputs:**
  - Local dashboard feed (HTTP POST to Streamlit's data endpoint or shared state file)
  - Local NDJSON archive on NVMe (all enriched events, for offline analysis)
  - Upstream HTTP POST (to Expanso Cloud or customer API)

### FR-6: Streamlit Dashboard
- Single page, accessible on LAN from any browser
- **Alignment mode:** Camera views with counting line overlay for calibration
- **Live counts:** Camera 1 departures, Camera 2 arrivals (large numbers)
- **Reconciliation status:** Match ✅ or Discrepancy ⚠️ with count
- **Event log:** Scrolling list of recent crossing events with timestamps
  ```
  2:47:23 PM  Camera 1  Box departed  (total: 7)
  2:47:26 PM  Camera 2  Box arrived   (total: 5)
  2:47:30 PM  ⚠️ Reconciliation: 7 departed, 5 arrived — 2 missing
  ```
- **Session controls:** New Session button, session ID display
- **"Powered by Expanso Edge"** in footer
- Auto-refreshes every 2 seconds

---

## Physical Setup — Exact Choreography

### Dock Door Demo Layout

```
  OUTSIDE (staging/truck side)          INSIDE (dock/receiving)

        CAMERA 1                              CAMERA 2
        [📷] (wall mount, 7ft)                [📷] (wall mount, 7ft)
         |   Reolink RLC-810A                  |   Reolink RLC-810A
         v   angled down ~25°                  v   angled down ~25°
  ┌──────────────┐                        ┌──────────────┐
  │  STAGING     │                        │  RECEIVING   │
  │  AREA        │    ┌──────────┐        │  AREA        │
  │  12 boxes    │    │ DOORWAY  │        │  (initially  │
  │  stacked     │    │          │        │   empty)     │
  │              │    │          │        │              │
  │  [TAPE LINE] │    │          │        │  [TAPE LINE] │
  └──────────────┘    └──────────┘        └──────────────┘

  Camera 1 sees:       Not visible         Camera 2 sees:
  staging area +       to either            receiving area +
  person leaving       camera               person entering
  through doorway                           through doorway
```

### Worker Choreography
1. Worker starts at staging area (outside), facing the stack
2. Picks up ONE box
3. Walks through doorway (crosses Camera 1's line → departure counted)
4. Continues through doorway into receiving area (crosses Camera 2's line → arrival counted)
5. Places box in receiving area
6. Returns through doorway empty-handed (NOT counted — directional + size filtering)
7. Repeat for all 12 boxes

### Demo 2 Variation (Discrepancy)
- Same choreography, but on boxes 6 and 9, worker sets the box down in the doorway or off to the side (visible to Camera 1 as a departure, but never crosses Camera 2's line)
- Camera 1: 12 departures. Camera 2: 10 arrivals. Dashboard: "⚠️ 2 missing"

### People Counting Setup (Demo 2)
- Reposition cameras at two hallway entrances (or keep dock door setup)
- Change config: `detect_classes: ["person"]`
- Restart pipeline
- Walk through both entrances multiple times
- Dashboard shows per-entrance people counts

### Alignment Check
Before each demo, dashboard shows alignment mode with camera views and counting line overlaid in green. Worker walks through once to verify correct counting direction. Dashboard confirms: "✅ Camera aligned — counting line active."

---

## Non-Functional Requirements

| Requirement | Target | Rationale |
|------------|--------|-----------|
| Setup time | < 30 minutes | Demo-ready from cold start |
| Continuous runtime | ≥ 15 minutes | Full demo without crash |
| Detection latency | < 3 seconds | Dashboard feels "live" |
| Inference throughput | ≥ 15 FPS per stream | YOLOv8s TensorRT FP16 benchmark: ~17 FPS at 2 streams |
| Box carry pace | 1 box per 3-5 seconds | Walking pace, enforced by choreography |
| False positive rate | 0 during recorded demo | Controlled environment, clean background |
| Offline buffer depth | ≥ 10,000 events | NVMe SSD handles this trivially |
| Build time | < 48 hours | Hard deadline |

---

## What Is Explicitly OUT of MVP

| Feature | Why It's Cut | When It Matters |
|---------|-------------|-----------------|
| DeepStream / GStreamer pipeline | PyAV + TensorRT is sufficient and faster to build | Production throughput optimization (v2) |
| Model fine-tuning | Class mapping in Expanso handles the label issue | Only if detection reliability fails |
| Zone polygon editor UI | Hardcode line coordinates | Multi-site deployment (v3) |
| OTA model updates via Expanso | Irrelevant for demo | Remote management (v3) |
| Cross-camera ReID | Overkill for counting — we're counting crossings, not tracking individuals across cameras | Identity tracking (v4) |
| Docker-compose stack | Run processes directly for fastest iteration | Reproducibility (v2) |
| Frame archiving / evidence recording | The 4K main stream could be recorded but adds storage complexity | v2 — enables "show me the frame" for discrepancy investigation |
| Multi-device fleet management | Single Jetson demo | v3 — Expanso Cloud manages fleet |

---

## Build Sequence (48 Hours)

### Pre-Build (Before Clock Starts)
- [ ] Jetson booted, SSH working, CUDA verified
- [ ] Both cameras PoE-connected, RTSP streams accessible from Jetson
- [ ] Expanso Edge installed on Jetson (`expanso-edge --version` works)
- [ ] YOLOv8 detects boxes through camera at demo distance (run quick inference test)

### MUST-HAVE (Hours 0-24)

**Hour 0-4: Single Camera Pipeline (inference only)**
1. RTSP ingest from one Reolink camera via PyAV
2. YOLOv8s TensorRT FP16 inference
3. ByteTrack tracking
4. Line-crossing counter with directional + size filtering
5. Output: raw CrossingEvents written to `raw-events.ndjson`
6. **Acceptance tests:** 5 boxes through → count = 5. 5 empty-handed returns → count still 5.

**Hour 4-8: Dual Camera + Raw Reconciliation**
1. Add second camera thread
2. Each camera writes to same `raw-events.ndjson` with different `camera_id`
3. Basic reconciliation logic (can be in Python for now — Expanso takes over in next step)
4. Test: carry boxes through doorway, verify both cameras count correctly

**Hour 8-14: Expanso Edge Pipeline**
1. Write Expanso pipeline YAML:
   - Input: file watcher on `raw-events.ndjson`
   - Processor: schema validation (Bloblang)
   - Processor: metadata enrichment (add device_id, site_id, session_id, model info)
   - Processor: class mapping (raw COCO class → business class)
   - Processor: windowed aggregation (30-second windows)
   - Processor: cross-camera reconciliation
   - Buffer: memory + disk
   - Output: local JSON state file for dashboard
   - Output: local NDJSON archive
   - Output: upstream HTTP (configurable endpoint)
2. Test pipeline standalone with sample events
3. Wire inference output → Expanso input → dashboard output
4. Test end-to-end: carry boxes, watch enriched events flow through Expanso to dashboard

**Hour 14-18: Streamlit Dashboard**
1. Build dashboard reading from Expanso's output (enriched events)
2. Live counts, reconciliation status, event log with timestamps
3. Alignment mode with camera view + line overlay
4. "New Session" button
5. "Powered by Expanso Edge" branding
6. Test readability at screen-recording resolution

**Hour 18-22: Physical Setup + Recording Demo 1 (Dock Door)**
1. Mount cameras on either side of doorway
2. Calibrate counting lines
3. Run full acceptance test (12 boxes through)
4. Screen-record dashboard + phone-record physical setup
5. Record Demo 1: clean transfer (12/12 match)
6. Reset session, record Demo 1b: discrepancy (12 vs 10)

**Hour 22-24: Record Demo 2 (People Counting)**
1. Change config to `detect_classes: ["person"]`
2. Reposition cameras if needed (or keep dock door — just count people walking through)
3. Record Demo 2: people counting at two entrances
4. Shows system is configurable — same hardware, same Expanso pipeline, different detection target

### STRETCH (Hours 24-30)

- Offline demo: unplug network during counting, show Expanso buffers locally, reconnect, data flushes upstream
- Dashboard polish: charts, better styling
- Code cleanup + README

### Buffer (Hours 30-48)
- Re-recording if needed
- Debugging edge cases
- Packaging and sending to customer

### Escalation Points

| Trigger | Action |
|---------|--------|
| Pre-build: RTSP doesn't work | Flash Reolink firmware, check network config. Reolink RTSP is well-documented — this should resolve quickly. |
| Hour 0: YOLO doesn't detect boxes | Map all COCO detections in counting zone above 0.3 confidence. Label doesn't matter — Expanso remaps it. |
| Hour 4: Counting unreliable | Tune direction threshold + size filter. Increase cooldown. |
| Hour 8: Expanso pipeline complexity | Start with minimal pipeline (input → output) and add processors incrementally. |
| Hour 14: Dashboard takes too long | Terminal printout of events works for recording. Dashboard is polish, not substance. |
| Hour 18: Physical setup issues | Move indoor. Use artificial lighting. |

---

## Recording Strategy

**The deliverable is three demo videos** (Demo 1, Demo 1b discrepancy, Demo 2 people counting).

1. **Screen recording (primary):** OBS/QuickTime records the Streamlit dashboard with event log showing enriched events flowing through Expanso
2. **Physical recording (context):** Phone captures box movement
3. **Voiceover opportunity:** "Watch the event log — each box crossing generates a schema-validated, enriched event through Expanso Edge. When box 11 left the outside but never arrived inside, Expanso's reconciliation processor flagged it within 30 seconds."

---

## Expanso Narrative — How to Position This

**What the demo shows the customer:**
- Inference is commodity — YOLO runs on a $250 Jetson
- **Expanso is the production platform** that makes raw detections useful:
  - Schema validation ensures data quality at the edge
  - Metadata enrichment makes every event self-describing and mergeable
  - Windowed aggregation reduces noise and bandwidth
  - Cross-camera reconciliation catches discrepancies automatically
  - Offline buffering means zero data loss at sites with unreliable connectivity
  - Upstream delivery integrates with any backend — no custom code

**The pitch:** "Anyone can run YOLO on a Jetson. What Expanso gives you is the production data pipeline that turns bounding boxes into auditable business events — validated, enriched, buffered, and delivered. At one site or fifty."

**When the customer asks about offline/eventual consistency:** "That's exactly what Expanso handles. The NVMe SSD buffers events during connectivity loss. When the network comes back, Expanso flushes in order — no gaps, no duplicates, no data loss. We can demo this: I'll unplug the ethernet, keep counting, plug it back in, and watch the events sync."

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Reolink RTSP latency or drops | Low | Medium | RTSP over TCP, auto-reconnect, sub-stream (720p) for lower bandwidth |
| YOLO doesn't detect boxes well | Medium | High | Class-agnostic detection in counting zone. Expanso remaps labels. |
| ByteTrack occlusion issues | Medium | Medium | Directional + size filtering as safety net. Controlled pacing. |
| Expanso pipeline complexity takes too long | Medium | Medium | Build incrementally. Start with input → output, add processors one at a time. |
| Jetson thermal throttling during extended demo | Low | Medium | JetPack 6.2 fan control. Run `sudo jetson_clocks`. Monitor temperature. |
| Build takes >24 hours for must-haves | Medium | High | People counting demo can be cut. Dock door demo is the priority. |

---

## Post-MVP Roadmap

### v2: Offline Demo + Frame Archiving (Week 2)
- Record discrepancy: unplug network, keep counting, reconnect, watch Expanso flush buffered events
- Enable frame archiving on 4K main stream for evidence retrieval
- "Show me the frame" feature: given a discrepancy event, retrieve the specific 4K frame

### v3: Production Deployment + Fleet (Week 3-4)
- Docker-compose for reproducible deployment
- Expanso Cloud for multi-site fleet management
- OTA model updates via Expanso pipeline
- Multiple Jetsons reporting to central dashboard

### v4: Remote Dashboard + Alerting (Week 4-6)
- Cloud dashboard accessible from Joe's phone
- SMS/email alerts on discrepancy events
- Historical trends and shift-level reporting

### v5: Smart Camera Positioning (Future)
- Automated pan/tilt tracking
- Customer: "If it could track location on its own, that would be very cool. More money then."

---

## Success Criteria

| Criteria | Target |
|----------|--------|
| Demo 1: Clean transfer | 12/12 counted correctly, "Counts Match" displayed, all events enriched through Expanso |
| Demo 1b: Discrepancy | 12 departures, 10 arrivals, DiscrepancyAlert generated with full metadata |
| Demo 2: People counting | System reconfigured and counting people with zero code changes |
| Event quality | Every event has schema_version, device_id, session_id, model metadata, timestamps |
| Expanso pipeline | Events flow: inference → Expanso validation → enrichment → aggregation → reconciliation → dashboard + archive |
| Offline resilience | (Stretch) Unplug network, counting continues, reconnect, events flush |
| False positives | Zero in recorded demos |
| Demo video length | 2-3 minutes per scenario |

---

## Open Questions

1. **What format does Ryan's collection API expect?** Need the exact endpoint and schema so Expanso's upstream output matches. We're sending enriched JSON events (see schema above) — not video.
2. **Does the customer want to see the demo live, or just recorded video?** If live, need stable 15-minute runtime. If recorded, can do multiple takes.
3. **Should the Expanso pipeline deploy from Expanso Cloud for the demo (showing cloud orchestration), or run locally?** Cloud deployment is more impressive but adds a dependency.
4. **For the people counting demo: same doorway, or should we set up a hallway/entrance scenario?** Same doorway is fastest; separate entrances shows more versatility.
