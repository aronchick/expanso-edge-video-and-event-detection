# Expanso Edge — Video & Event Detection

Edge sensors today ship every frame to the cloud and wait for someone to decide whether what they saw mattered. That costs you bandwidth on a contested link, latency on every decision, emissions an adversary can detect, and a single point of failure they will exploit.

This repo is a working reference for **moving the workload to the data**: YOLO + Gemini cascade on a Jetson, a FastAPI/WebSocket fusion node on a laptop, two RTSP cameras, multi-sector correlation, S3 archive — built on [Expanso Edge](https://expanso.io). Detection happens local; cloud is augmentation; pipeline updates push live; nothing is lost when the link drops.

---

## What it delivers

Five outcomes a perimeter operator would actually ask for:

1. **Detection runs local. Always.** YOLO v8 on a Jetson, sub-50ms inference. No network call required to know what you're looking at.
2. **Cloud is a bonus, not a precondition.** When a contact warrants richer context, the *edge* decides to call Gemini Flash for a description — only on the events that need it. The cloud being unreachable doesn't stop the sensor from working.
3. **Sensors correlate themselves.** A local fusion node merges contacts from N sensors within a configurable window. Multi-sector alerts fire without a round-trip to a TOC.
4. **Mission parameters update in seconds.** Push a new threat class (e.g., `drone`) once from the cloud control plane and every sensor on the network picks it up within ~1 second. No restart, no firmware push, no truck roll.
5. **Zero loss when the link drops.** When the WAN goes away the sensors keep detecting and the local dashboard keeps painting; events buffer to disk via Expanso Edge's offline queue. On reconnect, the cluster pulls the latest pipeline definition from the cloud and drains the buffered events to S3 — with the new transformation applied. Provenance preserved end-to-end.

---

## Run it on a laptop in 60 seconds

No GPU, no cameras, no Jetson required — uses synthetic events and procedurally generated camera feeds.

```bash
uv sync                                                # one-time

# Terminal 1 — fusion node (dashboard backend)
uv run edge-orchestrator --port 8080

# Terminal 2 — synthetic sensor pair
uv run edge-sensor --fake --multi --orchestrator http://localhost:8080 --cadence 0.6
```

Open `http://localhost:8080`, hit F11.

| Key | Action | Maps to |
|---|---|---|
| `F1` | Cloud DOWN — banner appears within ~3s (auto-detected by WAN probe) | outcome 5 |
| `F2` | Cloud UP — buffered events drain to S3 with current pipeline applied | outcome 5 |
| `F3` | Synthetic fused alert (full-screen takeover) | outcome 3 |
| `F4` | Push live trigger update — adds `drone` to the watch list across all sensors | outcome 4 |

The dashboard has two views: `OPS` (live operations — cameras, events, triggers, platform strip) and `ARCH` (light-themed architecture diagram with animated data-flow lines).

---

## Architecture

```
              ┌──────────────────────┐
              │   Expanso Cloud      │  ← control plane
              │                      │     (config + jobs only,
              │                      │      no customer data)
              └──────────┬───────────┘
                         │ dashed control plane
                         ▼
   ┌────────┐  RTSP  ┌─────────────────────────────┐  HTTP  ┌──────────────┐
   │ Camera │───────▶│  Jetson — Expanso Edge      │───────▶│ Local fusion │
   │ north  │        │   YOLO v8 GPU               │        │ node + dash  │
   └────────┘        │   trigger filter + cascade  │        └──────┬───────┘
   ┌────────┐  RTSP  │   DBOM sign + SQLite store  │               │
   │ Camera │───────▶│   offline replay queue      │               ▼
   │ south  │        └─────────────┬───────────────┘        ┌──────────────┐
   └────────┘                      │ fan-out                │ Browser:     │
                          ┌────────┼────────┐               │  WebRTC video│
                          ▼        ▼        ▼               │  WS events   │
                     ┌──────┐ ┌────────┐ ┌──────────┐       └──────────────┘
                     │Local │ │Gemini  │ │S3 archive│
                     │alerts│ │analyst │ │store-    │
                     │(corr)│ │per-evt │ │forward   │
                     └──────┘ └────────┘ └──────────┘
```

Every component runs as an Expanso job (`jobs/*.yaml`). On a real cluster you'll see four jobs in `expanso-cli job list`:

- `fusion-node` — local FastAPI backend (event store + cross-sensor correlator + dashboard)
- `sensor-north`, `sensor-south` — YOLO + Gemini cascade per camera
- `event-archive` — Bloblang pipeline that signs every event and ships to S3 with offline buffering

The dashed line is the **control plane**: job specs, trigger config, pipeline updates. The solid lines are **data plane**: video frames, events, S3 objects. Customer data never traverses Expanso Cloud — it's an architectural property of Expanso Edge, not a configuration choice.

---

## Repo layout

```
src/expanso_security_camera/
  sensor/        Edge sensor: pipeline, detector (YOLO+Gemini), emitter, dbom, triggers_client
  orchestrator/  FastAPI + WS backend: store, correlator, metrics, jobs_status, snapshots
  inference.py + simulate.py + dataset.py + finetune.py + recorder.py + server.py
                 The original box-counting demo variant — same engine, different application

public/
  edge/          Stage-scale dashboard (HTML/CSS/JS) — OPS dark theme + ARCH light theme
  index.html     Box-counting dashboard

jobs/
  sensor-north-job.yaml / sensor-south-job.yaml   YOLO + Gemini per camera
  orchestrator-job.yaml                           Local fusion node
  event-archive-job.yaml                          S3 archive Bloblang pipeline
  yolo-detector-job.yaml + security-camera-events-job.yaml   Box-counting variant

scripts/
  detect_loop.py / export_tensorrt.py / setup_jetson_lan.sh / run_sensor.sh
  install_jetson_update_timer.sh / jetson_update_expanso_edge.sh / rollback_engine.sh
  ingest_drone_video.py / ingest_3class.py / export_3class.py
  claude_relabel_drones.py / relabel_video.py

pipelines/      Bloblang pipelines
openspec/       Public design specs / change proposals
Dockerfile.jetson + Dockerfile.sensor
```

---

## Configuration

`config.yaml` (gitignored — copy from `config.example.yaml`) drives the box-counting variant. The Edge-ISR demo reads camera + cluster credentials from `.env`:

```bash
export CAM_USER=admin
export CAM_PASS_OUTSIDE=...     export CAM_PASS_INSIDE=...
export CAM_IP_OUTSIDE=...       export CAM_IP_INSIDE=...
export GEMINI_API_KEY=...
```

The reference deployment puts cameras on the wired LAN (192.168.2.x via PoE) and uses the Jetson's WiFi only for WAN egress — so the F1 demo can kill cloud reachability without touching the cameras.

---

## Build for a Jetson

```bash
# On the target Jetson:
python scripts/export_tensorrt.py            # produces yolo11s.engine

# Back on dev machine:
docker build -f Dockerfile.sensor -t ghcr.io/<you>/edge-isr-sensor:latest .
docker push ghcr.io/<you>/edge-isr-sensor:latest

# Deploy:
expanso-cli job deploy jobs/orchestrator-job.yaml
expanso-cli job deploy jobs/sensor-north-job.yaml
expanso-cli job deploy jobs/sensor-south-job.yaml
expanso-cli job deploy jobs/event-archive-job.yaml
expanso-cli job list
```

Cold-boot recovery on the reference Jetson is **~43 seconds** from `sudo reboot` to first HTTP 200 on `/metrics`. Camera tiles populate ~5–10s after that as CUDA + the YOLO TensorRT engine warm up.

---

## Development

```bash
uv sync --extra test
uv run ruff check . --fix
uv run ruff format .
uv run pytest                                  # 120+ tests
uv run pytest tests/test_counter.py::test_name # one test
```

---

## License

Apache-2.0 — see [LICENSE](./LICENSE). For the Expanso platform itself, see [expanso.io](https://expanso.io).
