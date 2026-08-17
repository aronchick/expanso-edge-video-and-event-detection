# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This repo holds two related Expanso Edge demos that share the same codebase:

1. **Edge-ISR** (the headline demo) — YOLO + Gemini cascade on the edge, FastAPI/WebSocket fusion node on a laptop, two cameras → **cross-zone people tally that MERGES both feeds into one combined count** → S3 archive with DDIL graceful degradation. The combined total across both cameras exceeding a threshold (default 5) throws a full-screen CROWD FLAG — neither zone alone trips it, the merge does. Stage-scale dashboard with `OPS` and `ARCH` views. Lives in `src/expanso_security_camera/sensor/` + `orchestrator/` + `public/edge/`. Booth run: two USB webcams via `edge-sensor --cameras 0,1`.
2. **Box-counting** — the original warehouse box-transfer demo. Two RTSP cameras (one outside a bay door, one inside) count boxes crossing a virtual line; the dashboard surfaces a "discrepancy" when arrivals don't match departures. Lives in `src/expanso_security_camera/{inference.py,server.py,counter.py,...}` and `public/index.html`.

Naming is non-obvious:
- Repo dir: `demo-drone-detection`
- Python package: `expanso_security_camera`
- The `internal/` directory is **gitignored** — it holds operational/deployment material (verbal demo scripts, cluster-specific bootstrap scripts, runbooks) that isn't part of the public product.

## Architecture (Edge-ISR — the primary demo)

**Sensor** (`src/expanso_security_camera/sensor/`)
- `pipeline.py` — `FreshFrameReader` with auto GStreamer (Jetson) / FFmpeg (Mac) selection
- `detector.py` — YOLO + Gemini cascade. The `TRIGGER_CLASSES` filter is polled from the fusion node every 1s for live class updates. The Gemini cooldown timestamp is updated even on failure — without that, every detection re-attempts Gemini and blocks the yolo worker for the full timeout.
- `emitter.py` — SQLite + offline replay queue + background drainer
- `dbom.py` — SHA-256 event signing
- `triggers_client.py` — 1s poll of `/triggers` for live class updates
- `main.py` — `edge-sensor` entrypoint; supports `--fake`, `--fake --multi`, `--offline-after`

**Orchestrator / fusion node** (`src/expanso_security_camera/orchestrator/`)
- `store.py` — SQLite event store + NDJSON tail (consumed by the archive Bloblang pipeline)
- `zones.py` — **the merge.** `ZoneCounter` sums per-zone person counts into a combined total and edge-triggers the CROWD FLAG when the combined total exceeds the threshold (default 5, `EDGE_CROWD_THRESHOLD`). Stale zones decay to 0; alert is cooldown-guarded and fires once per crossing. This is the headline alert path; broadcast as `{type:"zones"}` on every event + `GET /zones`.
- `correlator.py` — object-class alerts only (backpack/drone), 8s cooldown. The person rules were moved to `zones.py`; correlator stays silent unless the operator arms backpack/drone via the trigger bar.
- `triggers.py` — hot-reloadable trigger config (YAML on disk)
- `metrics.py` — rolling 60s events/min, totals, cloud-up flag
- `jobs_status.py` — shells out to `expanso-cli job list` (timeout 1s) for live cluster status. **Always wrap subprocess.run in `asyncio.to_thread`** when calling from an async handler — otherwise it blocks the FastAPI event loop and freezes WS + snapshot endpoints.
- `s3_watcher.py` — boto3 polling thread for S3 archive freshness
- `jetson_wan.py` — F1/F2 WAN-toggle SSH controller (cosmetic-only fallback when `ARMYX_JETSON_HOST` is unset)
- `snapshots.py` — serves real JPEGs from `snapshots/`, synthesizes 1280×720 frames in fake mode
- `api.py` — FastAPI: `/events`, `/triggers`, `/metrics`, `/jobs`, `/snapshot/{sector}`, `/demo/wan-{up,down}`, `/demo/fused-test`, `/ws`. Auto-detects WAN state via a periodic 1.1.1.1:443 probe and broadcasts cloud-up/down on transitions.

**Dashboard** (`public/edge/`)
- Two tab views: `OPS` (live operations — cameras + events + triggers + platform strip) and `ARCH` (light-themed architecture diagram with animated flow lines).
- WebRTC video via `go2rtc` on port 1984, single-shot `/snapshot/{sector}` JPEG fallback if WebRTC negotiation fails.
- Browser RTCPeerConnection uses `iceServers: []` — no STUN dependency, since Mac and Jetson are always on the same wired LAN.
- ARCH view uses inline SVG for the camera→Edge convergence + Edge→destinations fan-out curves. Curve geometry is rewritten on layout via `updateCameraCurves()` and `updateEdgeOutCurves()` in `ws_client.js` (reads element `getBoundingClientRect()` and writes path `d` attributes). Re-runs on load, resize, tab-switch, and any `.arch-view` size change.
- Light-theme ARCH view is a single `body.tab-arch` CSS variable override block; the OPS view stays dark-themed.
- All XSS-safe (`textContent` / `createElement`, never `innerHTML`).
- Dashboard assets are served with `Cache-Control: no-cache` so live edits pick up on simple reload (no hard-refresh required).

**Jobs** (`jobs/*.yaml`)
- `sensor-north-job.yaml`, `sensor-south-job.yaml` — sensors as Expanso `pipeline` jobs (subprocess input wrapping `edge-sensor`). The current Expanso CLI accepts only `type: pipeline`, not `Type: ops`, so the long-running detector process is supervised via `subprocess` + `restart_on_exit: true`.
- `orchestrator-job.yaml` — local FastAPI process (event store + cross-sensor correlator + dashboard backend). Job name is `fusion-node` (so the vocabulary doesn't clash with Expanso Cloud, which IS the cluster orchestrator). Filename uses `orchestrator-job` to match the binary name `edge-orchestrator`.
- `event-archive-job.yaml` — Bloblang pipeline tailing `events.ndjson`, validating DBOM signatures, fan-out to `aws_s3` (primary) + local NDJSON + stdout. Expanso Edge's offline buffer provides store-and-forward through WAN drops.

**Run on the laptop (no GPU, no cameras)** — fake crowd scenes:
```bash
uv run edge-orchestrator --port 8080
uv run edge-sensor --fake --multi --orchestrator http://localhost:8080 --cadence 0.6
open http://localhost:8080
```

**Run at the booth — through Expanso Edge (the real path)**. Capture, merge,
and archive all run as Expanso pipeline jobs (`jobs/*.yaml`), deployed with
`expanso-cli job deploy`. go2rtc bridges the two USB webcams → RTSP (consumed
by the `sensor-*` jobs) + WebRTC (dashboard video):
```bash
./scripts/render-go2rtc-yaml.sh && ./bin/go2rtc -config go2rtc.yaml &
expanso-cli profile select <profile>
for j in orchestrator-job sensor-north-job sensor-south-job fuse-job event-archive-job; do
  expanso-cli job deploy jobs/$j.yaml
done
```
The merge runs in TWO Expanso jobs: `fusion-node` (zones.py, drives the
dashboard) and the standalone observable `fuse` pipeline (scripts/fuse-correlator.py).
Threshold via `EDGE_CROWD_THRESHOLD` (set in both job specs).

**Non-Expanso smoke test only** (bypasses go2rtc + Expanso Edge — laptop
quick-check, NOT the booth path):
```bash
uv run edge-orchestrator --port 8080
# Find USB webcam capture indices (NOT esc-test-cameras — that probes the
# old Reolink RTSP cams). List AVFoundation devices and read the [N] index:
ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 | grep -i 'video devices' -A 10
uv run edge-sensor --cameras 0,1 --orchestrator http://localhost:8080
```
`--cameras 0,1` runs sensor-north (index 0) + sensor-south (index 1) as two
threads in ONE bare process. Webcam capture support lives in `pipeline.py`
(`as_device_index` → AVFoundation on Mac).

**Operator shortcuts** (keyboard focus on dashboard):
- `F1` → simulate WAN-down (cloud banner, red border, Gemini stripped from incoming events)
- `F2` → restore WAN
- `F3` → fire a synthetic CROWD FLAG takeover (rehearsal: 6 people = 3+3)
- `F4` → push the prepared trigger update (adds `backpack`+`drone` to the watch list)

**Hardware/Jetson side**:
- `Dockerfile.sensor` — L4T PyTorch base for GStreamer OpenCV
- `scripts/export_tensorrt.py` — one-shot engine builder, run on the target Jetson
- `scripts/setup_jetson_lan.sh` — runs **on the Jetson** as root: dnsmasq on `eth0`, NOPASSWD `nmcli` so F1/F2 don't prompt for password
- `scripts/install_jetson_update_timer.sh` — installs a systemd timer for nightly `expanso-edge` updates
- `scripts/rollback_engine.sh` — restore a previous TensorRT engine if the new one regresses

## Architecture (box-counting variant)

Three processes that communicate through files on disk, not a network bus:

1. **`scripts/detect_loop.py`** runs inside `ultralytics/ultralytics:latest-jetson-jetpack6` with `--runtime=nvidia`. Reads RTSP from each camera, runs YOLO, writes `snapshots/<camera_id>.jpg`, `detections.json`, and `detection-events.ndjson`. Only this process touches the GPU. Mounted into the container by `jobs/yolo-detector-job.yaml`, not built into an image.
2. **`esc-server`** (FastAPI, `src/expanso_security_camera/server.py`) runs on the Jetson host as a systemd unit (`scripts/esc-server.service`). Serves `public/index.html` and reads files written by the GPU process. Has no camera or YOLO dependency. Port 8080.
3. **Expanso Edge pipelines** (`jobs/*.yaml`):
   - `yolo-detector-job.yaml` launches the GPU container as an Expanso subprocess input.
   - `security-camera-events-job.yaml` tails `detection-events.ndjson` with pure Bloblang for schema validation, metadata enrichment, and lineage tracking. No Python.

The file-on-disk glue is deliberate: Expanso's subprocess input expects line-delimited stdout, and each process can restart independently without taking the others down.

### Two inference paths — pick one

`detect_loop.py` and `src/expanso_security_camera/inference.py` are **alternative** implementations, not layered:

- `detect_loop.py` — simple per-camera box counts, runs inside the Ultralytics container, prefers fine-tuned `box-detector-finetuned.pt` (6MB, ~60 FPS, conf 0.25) and falls back to `yolov8s-worldv2.pt` (1.2GB, ~60s cold start, conf 0.08, open-vocabulary classes).
- `inference.py` (`esc-infer`) — host-side path using ByteTrack + directional line counting, emits richer `CrossingEvent` objects with `direction` (inbound/outbound) and tracks discrepancy state in `state.json`.

Use `inference.py` when you want directional crossings; use `detect_loop.py` when you want raw per-camera counts shipped through Expanso.

## Commands

Dependencies via `uv`. Eight CLI scripts are exposed by `[project.scripts]` in `pyproject.toml`.

```bash
# Setup
uv sync                                 # installs deps including CLIP from git

# Lint, format, test (CI parity)
uv run ruff check . --fix
uv run ruff format .
uv run pytest                           # all tests
uv run pytest tests/test_counter.py     # one file
uv run pytest tests/test_counter.py::test_name  # one test
```

Running locally without cameras (box-counting):

```bash
uv run esc-server                       # dashboard at http://localhost:8080
uv run esc-simulate                     # generate fake events into state.json + NDJSON
uv run esc-simulate --discrepancy       # force a count mismatch
uv run esc-simulate --fast              # 0.5s cadence
uv run esc-simulate --people            # person mode instead of boxes
```

With real cameras (box-counting):

```bash
uv run esc-test-cameras                 # verify RTSP connectivity first
uv run esc-infer config.yaml            # host-side ByteTrack + line counting
uv run esc-record                       # capture raw camera footage
uv run esc-diagnose                     # dump detection diagnostics
```

Fine-tuning a camera-specific model:

```bash
# 1. Capture frames (parallel on the Jetson)
uv run esc-dataset capture config.yaml --boxes 1 --camera cam-inside --countdown 10 &
uv run esc-dataset capture config.yaml --boxes 2 --camera cam-outside --countdown 10 &
wait

# 2. Auto-label with YOLO, optionally validate with Claude CLI
uv run esc-dataset label
uv run esc-dataset validate              # optional
uv run esc-dataset export

# 3. Train
uv run esc-finetune dataset/yolo_dataset/data.yaml
# Output → runs/detect/box-finetune/weights/best.pt
# Copy that to /data/box-detector-finetuned.pt for detect_loop.py to pick up.
```

Deploying on a Jetson (box-counting):

```bash
expanso-cli job deploy jobs/yolo-detector-job.yaml          # GPU container
expanso-cli job deploy jobs/security-camera-events-job.yaml # Bloblang enrichment

sudo cp scripts/esc-server.service /etc/systemd/system/
sudo systemctl enable --now esc-server
```

`Dockerfile.jetson` is for baking the package into a custom image instead of bind-mounting via the Expanso job — it's not required for the standard deploy path above.

## Config

`config.yaml` (gitignored; copy from `config.example.yaml`) drives `esc-infer`. RTSP credentials and IPs come from environment variables expanded with `${VAR}` or `${VAR:default}`:

```bash
export CAM_USER=admin CAM_PASS_OUTSIDE=... CAM_PASS_INSIDE=...
export CAM_IP_OUTSIDE=...                CAM_IP_INSIDE=...
```

Switching between `box` and `person` modes requires editing `detect_mode`, `detect_classes`, and the `class_map` together. The class map intentionally covers both COCO names (suitcase, backpack, etc.) and YOLO-World prompt strings ("cardboard box", "shipping box") because either model can be active depending on whether a fine-tuned weight file is present.

## Runtime files (gitignored, but referenced everywhere)

- `state.json` — dashboard state (counts, discrepancy, recent events). Written by `inference.py` / `simulate.py`, read by `esc-server`.
- `commands.json` — control commands consumed by `esc-infer`.
- `snapshots/<camera_id>.jpg` — latest annotated frame per camera (written by `detect_loop.py` or sensor).
- `detections.json` — current per-camera detection counts (written by `detect_loop.py`).
- `detection-events.ndjson` — append-only event log; input to the Bloblang pipeline.
- `enriched-events.ndjson` — output of the Bloblang pipeline.
- `raw-events.ndjson` — output of `inference.py` / `simulate.py`.
- `events.ndjson` — append-only Edge-ISR event log (sensor emitter writes; archive Bloblang pipeline tails).
- `*.db` — SQLite stores: `orchestrator.db`, `sensor-north.db`, `sensor-south.db`.

## The `internal/` directory

`internal/` is **gitignored**. It holds operational material specific to particular deployments / live demos: verbal speaker scripts, cluster bootstrap scripts with named credentials, stage runbooks, planning docs. Not part of the public product. If you find yourself authoring something cluster- or demo-specific, put it in `internal/`.
