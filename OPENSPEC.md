# demo-security-camera

## Open Specification & Implementation Plan

**Version:** 0.1.0-draft
**Date:** March 2026
**Author:** Expanso
**Status:** Draft for Review

---

## 1. What This Is (And Why It Matters)

Security camera analytics is a $25B+ market dominated by proprietary, vertically-integrated stacks. If you want object counting, person tracking, or vehicle detection from camera feeds today, you're buying into one of two worlds:

**The Expensive World:** Enterprise NVR systems from Milestone, Genetec, or Avigilon. These run on beefy x86 servers, require per-camera licensing ($200–$500/camera/year), and lock you into vendor-specific ecosystems. A 4-camera deployment with analytics can easily run $10K–$20K in year one.

**The DIY World:** Open-source NVRs like Frigate, Shinobi, or ZoneMinder. These are great for recording and basic motion detection, but they don't solve the hard problem: running real inference at the edge, aggregating results across streams, and routing structured data upstream — all on commodity hardware, all without cloud round-trips.

**demo-security-camera** is a fully open, end-to-end reference implementation that proves you can do production-quality multi-stream object detection on a $249 NVIDIA Jetson Orin Nano — with Expanso handling the entire pipeline from RTSP ingest through inference orchestration to upstream data delivery.

The demo ships as:

1. **A Python package** (`expanso-security-camera`) installable via `pip install git+https://github.com/expanso-io/demo-security-camera.git` — contains the inference pipeline, stream management, and Expanso integration logic.
2. **A static results webpage** — pre-rendered HTML showcasing annotated frames, detection timelines, count charts, and heatmaps from sample footage. No hardware required to see what the system produces.
3. **A docker-compose stack** — spins up the full pipeline: RTSP ingest (from sample videos or live cameras), multi-stream inference, ByteTrack object tracking, Expanso edge agent for data routing, and a real-time results dashboard. `docker-compose up` and you're running.

The goal is to make this the definitive "here's how you do edge video analytics without a proprietary stack" reference — and to demonstrate that Expanso is the missing orchestration layer that makes it all work.


## 2. Target Use Cases

The system is configurable at deploy time — the user selects which COCO classes to detect and count. The demo ships with three pre-configured profiles:

**People Counting** — Retail foot traffic, building occupancy, event crowd monitoring. Counts people entering/exiting defined zones per camera. Outputs: count per zone per time window, occupancy over time, peak/trough analysis.

**Vehicle Counting** — Parking lot utilization, traffic flow, fleet staging areas. Counts vehicles by class (car, truck, bus, motorcycle). Outputs: count per class per time window, flow direction if zone-crossing is configured.

**General Object Counting** — Warehouse box counting (the Wall-E use case), package tracking, asset monitoring. User configures which COCO classes to track. Outputs: count per class per time window.

All three profiles use the same underlying pipeline — the only difference is the `--detect-classes` configuration passed at startup.

### 2.1 What's In v1 vs. What's On the Roadmap

**v1 (this demo):**
- Per-stream object detection and counting with configurable classes
- Per-stream object tracking via ByteTrack (persistent IDs within a single camera view)
- Multi-stream processing on a single Jetson device (2–4 streams)
- Structured count data routed upstream via Expanso
- Zone-based counting (user-defined ROI polygons per camera)
- Static results webpage + live docker-compose demo

**v2 (roadmap — specced but not implemented):**
- Cross-camera Re-Identification (ReID) — tracking the same individual/vehicle across multiple camera views
- Requires lightweight appearance embedding model (e.g., OSNet or a small ResNet-based feature extractor)
- Matching logic: appearance similarity + spatiotemporal constraints across overlapping/adjacent camera FOVs
- Expanso routes ReID events with global track IDs across streams

**v3 (roadmap):**
- Activity/behavior inference (dwell time, loitering detection, path analysis)
- Anomaly detection (unusual patterns, wrong-way movement, abandoned objects)


## 3. System Architecture

### 3.1 Architecture Overview

The system is a single-node edge deployment. One Jetson Orin Nano runs the entire pipeline:

```
┌─────────────────────────────────────────────────────────────────┐
│                     JETSON ORIN NANO                            │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────────┐  │
│  │ RTSP Source 1 │──▶│              │   │                    │  │
│  └──────────────┘   │   FFMPEG /   │   │   YOLO + ByteTrack │  │
│  ┌──────────────┐   │  GStreamer   │──▶│   Inference Engine  │  │
│  │ RTSP Source 2 │──▶│  Demux &    │   │   (TensorRT FP16)  │  │
│  └──────────────┘   │  Decode     │   │                    │  │
│  ┌──────────────┐   │  (NVDEC)    │   └─────────┬──────────┘  │
│  │ RTSP Source 3 │──▶│              │             │             │
│  └──────────────┘   └──────────────┘             │             │
│  ┌──────────────┐                                ▼             │
│  │ RTSP Source 4 │──▶        ┌──────────────────────────────┐  │
│  └──────────────┘           │         EXPANSO AGENT         │  │
│                             │                                │  │
│                             │  • Count aggregation per zone  │  │
│                             │  • Event structuring           │  │
│                             │  • Local buffering (offline)   │  │
│                             │  • Upstream data routing       │  │
│                             │  • OTA model updates           │  │
│                             └──────────────┬─────────────────┘  │
│                                            │                    │
│  ┌─────────────────────────────────────────┤                    │
│  │  LOCAL DASHBOARD (optional)             │                    │
│  │  Web UI on port 8080                    │                    │
│  └─────────────────────────────────────────┘                    │
└────────────────────────────────────────────┼────────────────────┘
                                             │
                                    LTE / WiFi / Ethernet
                                             │
                                             ▼
                              ┌──────────────────────────┐
                              │   UPSTREAM COLLECTOR      │
                              │   (API, S3, dashboard,    │
                              │    or custom endpoint)    │
                              └──────────────────────────┘
```

### 3.2 Why This Architecture

Three design decisions matter and are worth calling out:

**Everything runs on one device.** There's no "edge server + camera appliance" split. The Jetson ingests RTSP streams from standard IP cameras, decodes them using hardware NVDEC, runs inference on the GPU, tracks objects on CPU, and routes results via Expanso — all in a 15W power envelope. This is the whole point: commodity hardware doing what used to require a rack-mounted server.

**Expanso owns the full pipeline.** The Expanso agent isn't just a data shipper bolted onto the side. It orchestrates the pipeline: managing stream lifecycle, scheduling inference, aggregating count events, buffering during connectivity loss, handling OTA model updates, and routing structured results upstream. This is Expanso's edge compute thesis in a concrete, visible form.

**GStreamer over raw FFMPEG for stream management.** While FFMPEG is the go-to for video processing, GStreamer's pipeline model is a better fit here because NVIDIA's DeepStream SDK (built on GStreamer) provides hardware-accelerated decode (NVDEC), batched inference, and multi-stream muxing that's purpose-built for Jetson. The Python package wraps both — GStreamer/DeepStream for the real deployment, FFMPEG for the sample-video demo mode where DeepStream isn't available.


### 3.3 Component Breakdown

| Component | Technology | Role |
|-----------|-----------|------|
| Stream Ingest | GStreamer + NVDEC (live) / FFMPEG (sample mode) | Receive RTSP streams or read sample MP4s; decode frames using hardware acceleration |
| Inference Engine | YOLOv8s + TensorRT FP16 | Object detection on decoded frames; batched across streams for GPU efficiency |
| Object Tracker | ByteTrack | Lightweight multi-object tracking; assigns persistent IDs within each stream |
| Zone Counter | Custom Python | User-defined ROI polygons; counts objects crossing zone boundaries or present within zones |
| Expanso Agent | Expanso Edge | Pipeline orchestration, count aggregation, event buffering, upstream routing, OTA updates |
| Results Dashboard | Flask + HTMX (local) | Optional web UI showing live counts, annotated frames, and historical charts |
| Static Showcase | HTML + Chart.js | Pre-rendered results page for demos without hardware |


## 4. Hardware Specification

### 4.1 Primary Target: NVIDIA Jetson Orin Nano Super Developer Kit

| Spec | Value |
|------|-------|
| **GPU** | NVIDIA Ampere, 1024 CUDA cores, 32 Tensor Cores |
| **AI Performance** | Up to 67 TOPS (INT8) |
| **CPU** | 6-core ARM Cortex-A78AE @ 1.5 GHz |
| **RAM** | 8 GB LPDDR5 (unified CPU/GPU memory) |
| **Video Decode** | Software H.264: up to 11x 1080p30 streams |
| **Storage** | microSD + M.2 NVMe (recommended: 256GB NVMe for recording) |
| **Connectivity** | Gigabit Ethernet, M.2 Key E (WiFi/BT), USB 3.0 |
| **Power** | 7–15W (idle to full GPU load) |
| **Price** | ~$249 (developer kit) |
| **Software** | JetPack 6.1+, CUDA 12.6, TensorRT 10.3, DeepStream 7.1 |

### 4.2 Why Orin Nano

The Orin Nano occupies a specific sweet spot for this demo:

**It's real inference hardware, not a gimmick.** The 1024 CUDA cores and 32 Tensor Cores with TensorRT deliver actual production-grade inference. YOLOv8s on TensorRT FP16 runs at ~23ms per frame on Orin Nano — fast enough for 4 streams at 10+ FPS each when batched. This is not a "technically it runs but barely" situation; it's genuinely usable throughput.

**Hardware video decode matters enormously.** Decoding 4x 1080p30 RTSP streams in software would eat most of the CPU. The Orin Nano's decode pipeline handles this without touching the GPU or CPU compute budget, leaving those resources entirely for inference and tracking.

**The price point tells the story.** $249 for a device that handles 4 cameras with real-time AI analytics versus $10K+ for a proprietary NVR stack. That's the demo's punchline.

### 4.3 Camera Requirements

The system works with any IP camera that outputs RTSP/H.264 or H.265 streams. No vendor lock-in, no proprietary protocols.

Recommended specifications for cameras:
- Resolution: 1080p (the inference pipeline resizes to 640x640 for YOLO; higher resolution just means more decode work for marginal detection benefit)
- Frame rate: 15–30 FPS (inference runs on every Nth frame configurable via `--inference-interval`; 15 FPS source is sufficient for counting use cases)
- Codec: H.264 preferred (widest compatibility); H.265 supported but uses more decode resources
- Protocol: RTSP over TCP or UDP
- Typical cost: $30–$100 per camera (e.g., Reolink, Amcrest, Hikvision commodity models)

### 4.4 Bill of Materials — Complete 4-Camera Deployment

| Item | Unit Cost | Qty | Total |
|------|-----------|-----|-------|
| Jetson Orin Nano Super Dev Kit | $249 | 1 | $249 |
| M.2 NVMe SSD 256GB | $30 | 1 | $30 |
| IP Camera (1080p, RTSP, PoE) | $50 | 4 | $200 |
| PoE Network Switch (8-port) | $60 | 1 | $60 |
| Cat6 Ethernet Cables | $5 | 5 | $25 |
| USB LTE Modem (if no site WiFi/Ethernet) | $50 | 1 | $50 |
| Weatherproof Enclosure (Jetson) | $40 | 1 | $40 |
| Power Supply (Jetson + UPS) | $30 | 1 | $30 |
| **Total** | | | **~$684** |

No per-camera software licensing. No annual subscription fees. No cloud compute costs for inference.


## 5. Software Architecture — Detailed

### 5.1 Python Package Structure

```
demo-security-camera/
├── README.md
├── LICENSE                          # Apache 2.0
├── pyproject.toml                   # Package metadata, dependencies
├── setup.cfg
├── Dockerfile                       # Jetson-optimized container
├── Dockerfile.x86                   # x86 dev/demo container (no NVDEC)
├── docker-compose.yml               # Full stack: ingest + inference + expanso + dashboard
├── docker-compose.sample.yml        # Sample video mode (no cameras needed)
│
├── src/
│   └── expanso_security_camera/
│       ├── __init__.py
│       ├── cli.py                   # CLI entrypoint: `esc run`, `esc config`, `esc export`
│       │
│       ├── ingest/
│       │   ├── __init__.py
│       │   ├── stream_manager.py    # Manages multiple RTSP connections + reconnection
│       │   ├── gstreamer_pipeline.py # GStreamer/DeepStream pipeline builder (Jetson)
│       │   ├── ffmpeg_pipeline.py   # FFMPEG fallback for sample/x86 mode
│       │   └── frame_buffer.py      # Thread-safe frame queue per stream
│       │
│       ├── inference/
│       │   ├── __init__.py
│       │   ├── detector.py          # YOLO detection wrapper (TensorRT + ONNX fallback)
│       │   ├── tracker.py           # ByteTrack integration
│       │   ├── model_manager.py     # Model download, TensorRT engine build, versioning
│       │   └── batch_processor.py   # Multi-stream batched inference scheduler
│       │
│       ├── counting/
│       │   ├── __init__.py
│       │   ├── zone.py              # ROI polygon definitions and hit-testing
│       │   ├── line_counter.py      # Directional line-crossing counter
│       │   ├── area_counter.py      # Objects-present-in-zone counter
│       │   └── aggregator.py        # Time-windowed count aggregation
│       │
│       ├── expanso/
│       │   ├── __init__.py
│       │   ├── agent.py             # Expanso agent integration
│       │   ├── event_schema.py      # Structured event definitions (Pydantic models)
│       │   ├── buffer.py            # Local event buffer for offline resilience
│       │   └── router.py            # Upstream routing configuration
│       │
│       ├── dashboard/
│       │   ├── __init__.py
│       │   ├── app.py               # Flask app
│       │   ├── static/              # CSS, JS, Chart.js
│       │   └── templates/           # HTMX templates for live updates
│       │
│       └── config/
│           ├── __init__.py
│           ├── settings.py          # Pydantic settings model
│           ├── profiles/
│           │   ├── people.yaml      # Pre-configured for person detection
│           │   ├── vehicles.yaml    # Pre-configured for vehicle detection
│           │   └── warehouse.yaml   # Pre-configured for box/package detection
│           └── sample_zones/
│               ├── entrance.json    # Example zone polygon for entrance counting
│               └── parking.json     # Example zone polygon for parking lot
│
├── showcase/
│   ├── index.html                   # Static results showcase page
│   ├── assets/
│   │   ├── annotated_frames/        # Pre-rendered detection frames
│   │   ├── charts/                  # Pre-generated count charts (Chart.js data)
│   │   ├── heatmaps/               # Detection density heatmaps
│   │   └── sample_video_thumb/      # Video thumbnails
│   └── data/
│       └── sample_results.json      # Raw detection data for the showcase
│
├── sample_data/
│   ├── README.md                    # Attribution and license info for sample videos
│   ├── pedestrian_plaza.mp4         # Sample: people walking (from VIRAT or similar)
│   ├── parking_lot.mp4              # Sample: vehicles in parking area
│   ├── warehouse_dock.mp4           # Sample: boxes being moved
│   └── rtsp_simulator.py           # Script to serve sample videos as RTSP streams
│
├── tests/
│   ├── test_detector.py
│   ├── test_tracker.py
│   ├── test_zone_counter.py
│   ├── test_event_schema.py
│   └── test_integration.py
│
└── docs/
    ├── architecture.md
    ├── deployment_guide.md
    ├── configuration.md
    ├── model_matrix.md
    └── cross_camera_reid_spec.md    # v2 spec for cross-camera tracking
```

### 5.2 Stream Ingest Pipeline

The ingest layer handles the messy reality of IP camera streams: dropped connections, variable frame rates, network jitter, and codec negotiation.

**GStreamer/DeepStream path (Jetson production mode):**

The preferred path on Jetson uses GStreamer with NVIDIA's DeepStream plugins. This gives us:
- NVDEC hardware decode (zero CPU cost for video decode)
- `nvstreammux` for batching frames from multiple streams into a single GPU tensor
- Native TensorRT inference via `nvinfer`
- Built-in tracker support via `nvtracker`

The GStreamer pipeline per stream looks conceptually like:

```
rtspsrc → rtph264depay → nvv4l2decoder → nvstreammux → nvinfer → nvtracker → appsink
```

Frames arrive in the application's Python layer already decoded, batched, and (optionally) with inference results attached.

**FFMPEG fallback path (sample/x86 demo mode):**

For running the demo on a laptop or in CI without a Jetson, the FFMPEG path uses `ffmpeg` subprocess or `av` (PyAV) to decode video files or RTSP streams in software. Inference runs via ONNX Runtime (CPU or CUDA if available). This path is slower but functionally identical for evaluation and development.

**Stream resilience:**

- Automatic reconnection on RTSP stream failure (exponential backoff, max 60s)
- Frame drop detection and logging (if decode can't keep up, drop oldest frames, never block inference)
- Per-stream health metrics exposed to Expanso agent for monitoring


### 5.3 Inference Engine

#### Model Selection Matrix

The spec recommends tiered model options based on the deployment's stream count and FPS requirements. All benchmarks below are for the Jetson Orin Nano 8GB with TensorRT optimization at 640x640 input resolution:

| Model | Precision | Single-Stream FPS | 2-Stream FPS (each) | 4-Stream FPS (each) | mAP (COCO val) | Recommended Use |
|-------|-----------|-------------------|---------------------|---------------------|----------------|-----------------|
| YOLOv8-nano | FP16 | ~45 | ~22 | ~11 | 37.3 | Maximum stream count; accuracy-tolerant scenarios |
| YOLOv8-nano | INT8 | ~55 | ~27 | ~13 | ~36 | Best throughput; slight accuracy loss |
| **YOLOv8-small** | **FP16** | **~35** | **~17** | **~8** | **44.9** | **Recommended default — best accuracy/throughput balance** |
| YOLOv8-small | INT8 | ~43 | ~21 | ~10 | ~43 | Good throughput + accuracy |
| YOLOv8-medium | FP16 | ~20 | ~10 | ~5 | 50.2 | Accuracy-critical; 1–2 streams only |

**Default recommendation: YOLOv8-small at FP16.** This model handles 4 streams at ~8 FPS each — more than adequate for counting use cases where you don't need every single frame analyzed. With `--inference-interval=3` (run inference every 3rd frame on a 15 FPS source), you're analyzing 5 frames per second per stream, which is plenty for accurate counting with ByteTrack filling in the gaps.

#### TensorRT Engine Build

On first run, the system converts the ONNX model to a TensorRT engine optimized for the specific GPU. This takes 5–15 minutes and produces a `.engine` file cached for subsequent runs. The `model_manager.py` handles:
- Downloading the ONNX model from the GitHub release assets
- Building the TensorRT engine with the configured precision (FP16 or INT8)
- Caching engines by model version + precision + batch size
- OTA model updates via Expanso (new ONNX → rebuild engine)

#### Batched Multi-Stream Inference

Instead of running inference independently per stream (which wastes GPU scheduling overhead), the `batch_processor.py` collects one frame from each active stream, stacks them into a batch tensor, and runs a single batched inference call. This is critical for throughput — a batch of 4 is significantly faster than 4 sequential single-frame calls.

The batch scheduler runs on a configurable tick (default: every 100ms for 10 FPS effective inference rate). It grabs the latest frame from each stream's frame buffer, batches them, runs inference, and dispatches results back to each stream's tracker.


### 5.4 Object Tracking — ByteTrack

ByteTrack is the right choice here for several reasons:

- **Lightweight.** It runs entirely on CPU with negligible overhead (<1ms per frame for typical scene densities). The GPU stays free for inference.
- **No appearance model required.** ByteTrack uses IoU-based association with a two-stage matching process (high-confidence detections first, then low-confidence). This means it doesn't need a separate feature extraction model — important when GPU memory is at a premium on an 8GB device.
- **Well-proven on Jetson.** ByteTrack + YOLO on Jetson is a documented, battle-tested combination used in production by drone tracking systems, retail analytics, and industrial inspection.

ByteTrack assigns a persistent `track_id` to each detected object within a stream. This ID is stable across frames as long as the object remains visible (with brief occlusion tolerance). The `track_id` is included in all count events sent upstream via Expanso, enabling de-duplication: if the same person walks past a counting line twice, they're counted once (because the track ID is the same).

**Limitations (and why ReID is v2):**

ByteTrack track IDs are per-stream. If a person appears in Camera 1, walks out of frame, and appears in Camera 2, they get a new track ID. Unifying these requires a cross-camera Re-Identification (ReID) model — an appearance embedding that can match the same person across views. This is a meaningfully harder problem that requires an additional lightweight CNN (e.g., OSNet-x0.25, ~2M params) and a matching/gallery system. It's specced in `docs/cross_camera_reid_spec.md` for v2 but not implemented in the demo.


### 5.5 Zone-Based Counting

Raw detection counts ("there are 3 people in frame") are less useful than zone-aware counts ("12 people entered through the north door in the last hour"). The counting module supports two modes:

**Line Crossing Counter:** The user defines a line (two points) in the camera's frame coordinates. The counter tracks when an object's centroid crosses the line, noting the direction. This is how you count entries/exits through a doorway or vehicles passing a point.

**Area Presence Counter:** The user defines a polygon (any number of points). The counter reports how many tracked objects are currently inside the polygon. This is how you measure zone occupancy — how many people are in the lobby, how many cars are in the parking section.

Zone definitions are stored as JSON files (see `config/sample_zones/`) and can be configured per-camera via the dashboard or YAML config.

Counting output is time-windowed (configurable, default: 1 minute). Every window, the aggregator produces a structured count event:

```json
{
  "stream_id": "camera-north-entrance",
  "window_start": "2026-03-13T14:00:00Z",
  "window_end": "2026-03-13T14:01:00Z",
  "zone": "entrance-line-1",
  "zone_type": "line_crossing",
  "counts": {
    "person": {"in": 7, "out": 3},
    "bicycle": {"in": 1, "out": 0}
  },
  "unique_tracks": {
    "person": 8,
    "bicycle": 1
  }
}
```


### 5.6 Expanso Integration

This is the core of the demo's value proposition. Expanso isn't a sidecar — it's the orchestration layer.

#### What Expanso Manages

**Pipeline lifecycle:** Expanso starts and stops the inference pipeline based on schedule or trigger. For a warehouse that only operates during shift hours, Expanso can spin up inference at 6 AM and shut it down at 6 PM, saving power and extending hardware life.

**Stream management:** Expanso monitors stream health (is the RTSP connection alive? Is the frame rate degraded? Is decode falling behind?) and can restart individual streams without disrupting others.

**Count event aggregation:** Raw per-frame detections are noisy. Expanso aggregates these into time-windowed count events with de-duplication via track IDs before routing upstream. This reduces upstream data volume by 100–1000x versus sending raw detections.

**Offline buffering:** When upstream connectivity is lost (common at edge sites), Expanso buffers count events locally on the NVMe SSD. When connectivity restores, it flushes the buffer in order. No data loss, no gaps in the count timeline.

**Upstream routing:** Expanso routes structured events to configurable destinations: a REST API endpoint, S3 bucket, MQTT broker, or any other supported sink. The demo ships with a local dashboard sink and a configurable HTTP POST endpoint.

**OTA model updates:** When a new model version is available (e.g., a retrained YOLO with site-specific classes), Expanso handles downloading the new ONNX file, triggering a TensorRT rebuild, and hot-swapping the model with minimal inference downtime.

#### Event Schema

All events routed through Expanso follow a consistent schema (defined in `expanso/event_schema.py` as Pydantic models):

```python
class CountEvent(BaseModel):
    event_type: str = "count"
    source_id: str          # Expanso data source ID
    stream_id: str          # Camera identifier
    window_start: datetime
    window_end: datetime
    zone_id: str
    zone_type: str          # "line_crossing" | "area_presence"
    counts: dict[str, dict[str, int]]  # class -> {direction: count}
    unique_tracks: dict[str, int]      # class -> unique track count
    device_id: str          # Jetson device identifier
    model_version: str      # YOLO model version used
    confidence_threshold: float

class HealthEvent(BaseModel):
    event_type: str = "health"
    device_id: str
    streams: list[StreamHealth]  # per-stream: connected, fps, decode_lag, inference_fps
    gpu_utilization: float
    gpu_memory_used_mb: float
    cpu_utilization: float
    temperature_c: float
    uptime_seconds: int
    buffer_depth: int       # queued events waiting to flush upstream
```


## 6. Docker Compose Topology

### 6.1 Sample Mode (No Cameras Required)

`docker-compose -f docker-compose.sample.yml up`

This mode is for demos, evaluation, and development. It serves sample video files as RTSP streams, processes them through the full pipeline, and displays results on the dashboard.

```
┌─────────────────────────────────────────────────┐
│              docker-compose.sample.yml           │
│                                                  │
│  ┌──────────────────┐                            │
│  │  rtsp-simulator  │  Serves sample .mp4 files  │
│  │  (rtsp-simple-   │  as RTSP streams on        │
│  │   server + ffmpeg)│  rtsp://localhost:8554/    │
│  └────────┬─────────┘  stream1, stream2, etc.    │
│           │                                      │
│           ▼                                      │
│  ┌──────────────────┐                            │
│  │  inference        │  Pulls RTSP streams,      │
│  │  (expanso-sec-   │  runs YOLO + ByteTrack,   │
│  │   camera)         │  produces count events    │
│  └────────┬─────────┘                            │
│           │                                      │
│           ▼                                      │
│  ┌──────────────────┐                            │
│  │  expanso-agent    │  Aggregates events,       │
│  │                   │  routes to dashboard      │
│  └────────┬─────────┘                            │
│           │                                      │
│           ▼                                      │
│  ┌──────────────────┐                            │
│  │  dashboard        │  Flask + HTMX on :8080    │
│  │                   │  Live counts + charts     │
│  └──────────────────┘                            │
│                                                  │
└─────────────────────────────────────────────────┘
```

**Services:**

| Service | Image | Purpose | Ports |
|---------|-------|---------|-------|
| `rtsp-simulator` | `aler9/rtsp-simple-server` + `ffmpeg` sidecar | Loops sample videos as RTSP streams | 8554 (RTSP) |
| `inference` | `expanso/security-camera:latest` | Multi-stream inference pipeline | — (internal) |
| `expanso-agent` | `expanso/agent:latest` | Event aggregation and routing | — (internal) |
| `dashboard` | `expanso/security-camera:latest` (dashboard mode) | Web UI | 8080 (HTTP) |

### 6.2 Live Mode (Real Cameras)

`docker-compose up`

Identical to sample mode but `rtsp-simulator` is replaced with configuration pointing to real camera RTSP URLs. The `config.yaml` maps camera URLs to stream IDs and zone definitions.

### 6.3 Configuration

All configuration is via a single `config.yaml` mounted into the inference container:

```yaml
# config.yaml
device:
  id: "jetson-site-alpha"
  model: "yolov8s"
  precision: "fp16"
  inference_interval: 3        # Run inference every Nth frame
  confidence_threshold: 0.4
  detect_classes: ["person"]   # COCO class names to detect

streams:
  - id: "north-entrance"
    url: "rtsp://192.168.1.100:554/stream1"
    zones:
      - id: "entry-line"
        type: "line_crossing"
        points: [[100, 400], [540, 400]]
        direction: "vertical"   # Count up/down crossings

  - id: "parking-lot"
    url: "rtsp://192.168.1.101:554/stream1"
    zones:
      - id: "lot-area"
        type: "area_presence"
        points: [[50, 50], [590, 50], [590, 430], [50, 430]]

expanso:
  upstream:
    type: "http"
    url: "https://api.example.com/v1/events"
    auth_header: "Bearer ${ESC_API_TOKEN}"
  buffer:
    max_events: 10000
    flush_interval_seconds: 30
  health_report_interval_seconds: 60

dashboard:
  enabled: true
  port: 8080
```


## 7. Static Showcase Webpage

The showcase page (`showcase/index.html`) is a single-file HTML page (no build step, no framework) that demonstrates what the system produces. It's designed to be opened in a browser directly from the repo or served from any static host.

**Sections:**

1. **Hero:** "Edge Video Analytics on a $249 Device" — one-sentence pitch, cost comparison table vs. proprietary stacks
2. **How It Works:** Architecture diagram (simplified), 4-step explanation
3. **Live Results (Pre-Recorded):** Annotated video frames showing bounding boxes and track IDs from each sample video. Embedded as images, not video (keeps it static and fast).
4. **Count Charts:** Chart.js line graphs showing count-over-time for each sample video — people entering/exiting, vehicles in lot, boxes unloaded. Data sourced from `showcase/data/sample_results.json`.
5. **Detection Heatmap:** Canvas-rendered heatmaps showing where in each camera view detections are most concentrated. Gives an immediate visual sense of traffic patterns.
6. **Hardware & Cost:** BOM table, power consumption, comparison to proprietary alternatives
7. **Get Started:** Clone, docker-compose up, done. Three commands.

The page uses no external CDN dependencies — everything (Chart.js, fonts, styles) is vendored in `showcase/assets/` so it works offline.


## 8. Cross-Camera Re-Identification (v2 Specification)

This section specifies the v2 cross-camera ReID system for future implementation. It is NOT part of the v1 demo.

### 8.1 Problem Statement

When the same individual appears in multiple camera views, v1 assigns them different track IDs in each view. v2 aims to unify these into a single global ID across cameras, enabling:
- Accurate unique visitor counts across a multi-camera site
- Path analysis (person entered via Camera 1, spent 5 min in Camera 2's zone, exited via Camera 3)
- Dwell time across zones (not just within a single camera's view)

### 8.2 Approach

**Appearance Embedding Model:** Add a lightweight feature extractor (OSNet-x0.25, ~2M parameters, ~1ms per crop on Orin Nano) that produces a 512-dim embedding vector for each detected person/vehicle crop. This runs on the GPU after detection, only on newly-detected objects (not every frame).

**Gallery System:** Maintain a rolling gallery of recent appearance embeddings keyed by (stream_id, track_id). When a new track appears in any stream, compare its embedding against the gallery using cosine similarity. If similarity exceeds a threshold AND spatiotemporal constraints are met (the cameras are adjacent, the timing is plausible), assign the existing global ID.

**Spatiotemporal Constraints:** Pure appearance matching produces false positives (many people wear similar clothing). Constrain matches by camera adjacency graph and transition time: if Camera 1 and Camera 3 have no physical path shorter than 2 minutes, reject any match with <2 minute gap.

**Expanso's Role:** Expanso routes ReID match events alongside count events. Global track IDs are included in count events for v2-aware consumers. Expanso also manages the camera adjacency graph configuration.

### 8.3 Estimated Performance Impact

Adding OSNet-x0.25 with TensorRT FP16 on Orin Nano: ~1ms per detection crop. For a scene with 10 simultaneous people across 4 cameras, that's ~10ms extra per inference cycle — negligible relative to the ~23ms YOLO inference time. Memory overhead: ~50MB for the OSNet engine + gallery storage.


## 9. Performance & Sizing Guide

### 9.1 Stream Capacity by Configuration

| Config | Model | Precision | Streams | FPS/Stream | GPU Util | RAM Used |
|--------|-------|-----------|---------|-----------|----------|----------|
| Light | YOLOv8n | INT8 | 4 | ~13 | ~70% | ~3.5 GB |
| **Balanced** | **YOLOv8s** | **FP16** | **4** | **~8** | **~85%** | **~4.5 GB** |
| Quality | YOLOv8m | FP16 | 2 | ~10 | ~90% | ~5.5 GB |
| Max Streams | YOLOv8n | INT8 | 6 | ~7 | ~95% | ~5.0 GB |

Notes:
- FPS figures are per-stream with batched inference and `--inference-interval=1` (every frame)
- With `--inference-interval=3` on 15 FPS sources, effective analysis rate is 5 FPS/stream regardless, and GPU utilization drops significantly
- RAM includes OS, inference engine, tracker, and Expanso agent
- DeepStream's hardware decode is effectively free (no GPU/CPU impact)

### 9.2 Comparison to Proprietary Alternatives

| | demo-security-camera | Genetec Security Center | Milestone XProtect | AWS Panorama |
|---|---|---|---|---|
| **Hardware Cost (4 cam)** | ~$684 | $3,000–$8,000 (server) | $2,000–$5,000 (server) | $5,000 (appliance) |
| **Software License** | $0 (open source) | $200–$500/cam/year | $150–$400/cam/year | $8.33/cam/month |
| **Year 1 Total (4 cam)** | ~$684 | $4,500–$12,000 | $3,200–$8,000 | $5,400 |
| **Year 3 Total (4 cam)** | ~$684 | $6,500–$16,000 | $4,800–$12,000 | $5,800 |
| **Cloud Dependency** | None | Optional | Optional | Required |
| **Edge Inference** | Yes (on-device) | No (server-based) | No (server-based) | Yes (limited) |
| **Open Models** | Any ONNX/TensorRT | Vendor models only | Vendor models only | AWS models only |
| **Offline Operation** | Full (Expanso buffering) | Partial | Partial | Degraded |
| **Power Draw** | 15W | 200–500W | 200–500W | 75W |

The cost story is the demo's strongest selling point. Three years of operation for a 4-camera deployment costs $684 total with this system versus $5,000–$16,000 with proprietary stacks. And you own the hardware, the models, and the data pipeline.


## 10. Implementation Plan

### Phase 1: Core Pipeline Validation (Weeks 1–3)

**Goal:** Prove that the inference pipeline runs on Jetson Orin Nano with target throughput.

| Task | Owner | Duration | Dependencies |
|------|-------|----------|-------------|
| Set up Jetson Orin Nano dev environment (JetPack 6.1, DeepStream 7.1) | Expanso Eng | 2 days | Hardware procurement |
| Build and test GStreamer/DeepStream multi-stream pipeline (2 streams from file) | Expanso Eng | 3 days | Dev environment ready |
| Integrate YOLOv8s TensorRT FP16 inference in batched mode | Expanso Eng | 3 days | Pipeline working |
| Integrate ByteTrack tracker | Expanso Eng | 2 days | Inference working |
| Benchmark: measure actual FPS per stream at 2 and 4 streams | Expanso Eng | 1 day | Tracker integrated |
| Build FFMPEG fallback path for x86 development | Expanso Eng | 3 days | Parallel with above |
| **Phase 1 Gate:** 4 streams at ≥8 FPS each with YOLOv8s FP16 | | | |

### Phase 2: Expanso Integration & Counting Logic (Weeks 3–5)

**Goal:** Wire up Expanso agent, implement zone counting, get structured events flowing.

| Task | Owner | Duration | Dependencies |
|------|-------|----------|-------------|
| Implement zone counting module (line crossing + area presence) | Expanso Eng | 3 days | Phase 1 complete |
| Implement count aggregator (time-windowed events) | Expanso Eng | 2 days | Zone counting working |
| Integrate Expanso agent on Jetson | Expanso Eng | 3 days | Expanso ARM support confirmed |
| Define and implement event schema (CountEvent, HealthEvent) | Expanso Eng | 2 days | Parallel |
| Wire aggregator → Expanso agent → upstream HTTP sink | Expanso Eng | 2 days | Agent integrated |
| Test offline buffering: kill connectivity, verify buffer + flush | Expanso Eng | 1 day | Integration complete |
| **Phase 2 Gate:** End-to-end event flow from camera → count → Expanso → upstream | | | |

### Phase 3: Demo Polish & Packaging (Weeks 5–7)

**Goal:** Ship the demo — repo, package, docker-compose, showcase page.

| Task | Owner | Duration | Dependencies |
|------|-------|----------|-------------|
| Build Docker containers (Jetson + x86) | Expanso Eng | 2 days | Phase 2 complete |
| Build docker-compose.yml (live mode) and docker-compose.sample.yml | Expanso Eng | 2 days | Containers built |
| Build RTSP simulator (rtsp-simple-server + sample video loop) | Expanso Eng | 1 day | Sample videos sourced |
| Source and prepare sample videos (pedestrian, parking, warehouse) | Expanso Eng | 2 days | License verification |
| Build local dashboard (Flask + HTMX) | Expanso Eng | 3 days | Event schema stable |
| Build static showcase webpage | Expanso Eng | 3 days | Sample results generated |
| Package as pip-installable (pyproject.toml, CLI entrypoint) | Expanso Eng | 1 day | Code structure finalized |
| Write README, deployment guide, configuration docs | Expanso Eng | 2 days | Everything working |
| Publish to GitHub with sample data and release assets | Expanso Eng | 1 day | All docs ready |
| **Phase 3 Gate:** External user can clone repo, run docker-compose up, see results | | | |


## 11. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Expanso agent doesn't run on ARM/Jetson | Medium | Critical | Validate early in Phase 1; have x86-only fallback where agent runs on a companion device |
| YOLOv8s FP16 throughput below target at 4 streams | Low | High | Fall back to YOLOv8n; increase inference interval; reduce to 3 streams in marketing |
| 8GB unified memory insufficient for 4-stream batched inference + tracker + agent | Medium | High | Profile early; tune batch size; consider Orin NX 16GB as alternative target |
| Sample video licensing issues | Low | Medium | Use VIRAT dataset (public domain/government funded) or record custom footage |
| DeepStream version incompatibility with JetPack | Low | Medium | Pin to JetPack 6.1 + DeepStream 7.1; test in container for reproducibility |
| RTSP stream instability in production deployments | High | Medium | Automatic reconnection with backoff; per-stream health monitoring via Expanso |
| TensorRT engine build fails on specific hardware | Low | Medium | Ship pre-built engines for common configs; fallback to ONNX Runtime |


## 12. Open Questions

| Question | Priority | Owner | Status |
|----------|----------|-------|--------|
| Does Expanso agent currently support ARM64 / JetPack? | **Must Have** | Expanso Eng | Needs confirmation |
| What's the minimum Expanso agent version required? | Must Have | Expanso Eng | TBD |
| Can Expanso manage GStreamer pipeline lifecycle directly? | Should Have | Expanso Eng | TBD — would simplify architecture |
| Sample video sourcing: VIRAT dataset vs. recording custom? | Must Have | Expanso Eng | VIRAT preferred for licensing clarity |
| Does the demo target JetPack 6.1 or also need to support 5.x? | Should Have | Expanso Eng | Recommend 6.1+ only |
| Dashboard build: should this be a separate standalone project or embedded? | Should Have | Expanso Eng | Recommend embedded for demo simplicity |
| GitHub release asset hosting for ONNX models (size limits?) | Nice to Have | Expanso Eng | GitHub LFS or external hosting |


## 13. Glossary

**ByteTrack** — A lightweight multi-object tracking algorithm that associates detections across frames using IoU (Intersection over Union) matching. Runs on CPU with negligible overhead.

**COCO** — Common Objects in Context. A standard dataset and class taxonomy for object detection. YOLOv8 is typically trained on COCO's 80 classes (person, car, truck, bicycle, etc.).

**DeepStream** — NVIDIA's SDK for building AI-powered video analytics applications, built on GStreamer. Provides hardware-accelerated decode, inference, tracking, and multi-stream muxing.

**Expanso Agent** — Expanso's edge runtime that manages data pipeline orchestration, local buffering, upstream routing, and OTA updates on edge devices.

**GStreamer** — An open-source multimedia framework for building media processing pipelines. NVIDIA's DeepStream extends GStreamer with GPU-accelerated video analytics plugins.

**NVDEC** — NVIDIA's hardware video decoder. Decodes H.264/H.265 video streams without using GPU compute cores or CPU, freeing both for inference.

**ONNX** — Open Neural Network Exchange. A portable model format that can be converted to TensorRT for optimized inference on NVIDIA GPUs.

**ReID (Re-Identification)** — The task of recognizing the same individual across different camera views using appearance features. Planned for v2.

**ROI (Region of Interest)** — A user-defined polygon in camera frame coordinates used for zone-based counting.

**RTSP** — Real-Time Streaming Protocol. The standard protocol for IP cameras to transmit live video. Typically carries H.264 or H.265 encoded streams.

**TensorRT** — NVIDIA's SDK for high-performance deep learning inference. Optimizes models through layer fusion, precision calibration (FP16/INT8), and hardware-specific kernel selection.

**YOLOv8** — "You Only Look Once" version 8, by Ultralytics. A state-of-the-art real-time object detection model available in nano, small, medium, large, and extra-large variants.

---

## 14. Version Roadmap

| Version | Focus | Key Features |
|---------|-------|-------------|
| **v1.0** | Per-stream detection & counting | Multi-stream YOLO + ByteTrack; zone counting; Expanso pipeline; docker-compose demo; static showcase |
| **v2.0** | Cross-camera intelligence | ReID across camera views; global track IDs; path analysis; automated manifest/schedule integration |
| **v3.0** | Behavioral analytics | Dwell time analysis; loitering detection; anomaly flagging; activity classification |

---

*This specification is open source under Apache 2.0. Contributions welcome.*
