# Edge ISR — live demo repo

This is the **internal, demo-specific** repository for the Edge ISR live demo: YOLO + Gemini cascade on a Jetson, FastAPI/WebSocket fusion node on a laptop, two RTSP cameras, multi-sector correlation, S3 archive, DDIL graceful degradation. It includes deployment-specific tooling (cluster bootstrap, S3 wiring, systemd units), demo-day runbooks, and verbal speaker scripts that are intentionally not part of the public product reference.

> **Looking for the clean public reference instead?** → [`aronchick/expanso-edge-video-and-event-detection`](https://github.com/aronchick/expanso-edge-video-and-event-detection)
> That repo holds the same source tree but stripped of demo-specific files, with a generic README and the operational material removed.

---

## What's in here vs. what's not

| | |
|---|---|
| **In git (public to this repo)** | `src/`, `jobs/`, `scripts/`, `public/`, `tests/`, `pipelines/`, `openspec/`, `Dockerfile.{jetson,sensor}`, `pyproject.toml`, `config.example.yaml` |
| **In `internal/` (gitignored, distributed via scp/share)** | Verbal demo scripts (`DEMO_SCRIPT.md`, `DEMO_SCRIPT_1MIN.md`), the printable stage runbook (`STAGE_RUNBOOK.md`), implementation reference (`HACKATHON_SCRIPT.md`), UI design notes (`DEMO_UI_SPEC.md`), planning docs (`PLAN.md`, `OPENSPEC.md`), cluster-specific bootstrap scripts (`scripts/bootstrap_armyx_tech.sh`, etc.), session handoff notes |
| **In `.armyx-tech-secrets/` (gitignored)** | AWS access keys for the demo S3 bucket |
| **`.env` (gitignored)** | RTSP camera credentials, Expanso Cloud API key, Jetson host |

---

## Run the demo on a laptop in 60 seconds

No GPU, no cameras, no Jetson required.

```bash
uv sync                                                # one-time

# Terminal 1 — fusion node (dashboard backend)
uv run edge-orchestrator --port 8080

# Terminal 2 — synthetic sensor pair
uv run edge-sensor --fake --multi --orchestrator http://localhost:8080 --cadence 0.6
```

Open `http://localhost:8080`, hit F11.

**Operator shortcuts** (keyboard focus on dashboard):

| Key | Action |
|---|---|
| `F1` | Cloud DOWN — banner appears within ~3s (auto-detected by WAN probe) |
| `F2` | Cloud UP |
| `F3` | Synthetic fused alert (full-screen takeover) |
| `F4` | Push live trigger update — adds `drone` to the watch list across all sensors |

---

## Run the demo on the live Jetson

The Jetson runs `expanso-edge` as a systemd unit, which supervises four jobs (`fusion-node`, `sensor-north`, `sensor-south`, `armyx-tech-event-archive`). Cold-boot recovery is **~43 seconds** from `sudo reboot` to first HTTP 200 on `/metrics`.

Day-1 bootstrap (creates S3 bucket, IAM user, distributes credentials, deploys pipeline):

```bash
JETSON_HOST=nvidia@jetson.local \
  ARMYX_EXPANSO_ENDPOINT=https://<your-cluster>.cloud.expanso.io:9010 \
  ARMYX_EXPANSO_API_KEY=exp_ak_... \
  ./internal/scripts/bootstrap_armyx_tech.sh

# On the Jetson, once:
sudo ./scripts/setup_jetson_lan.sh
```

Demo-day operational details (pre-flight checklist, recovery moves, network topology, the 5 wired-up automatic behaviors): see `internal/STAGE_RUNBOOK.md`.

Verbal scripts for the live demo: `internal/DEMO_SCRIPT.md` (3:00 record version) and `internal/DEMO_SCRIPT_1MIN.md` (1:00 cold-intro version).

---

## Architecture (high level)

```
              ┌──────────────────────┐
              │   Expanso Cloud      │  ← control plane (config + jobs only,
              │                      │     no customer data passes through)
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

For the full architecture write-up (network topology, file paths on the Jetson, supervision model, the 5 WAN-down auto-fixes), see `internal/STAGE_RUNBOOK.md` § "Network topology & known timings".

---

## Repo layout

```
src/expanso_security_camera/
  sensor/        Edge sensor: pipeline, detector, emitter, dbom, main, triggers_client
  orchestrator/  FastAPI + WS, store, correlator, metrics, jobs_status, snapshots, triggers
  inference.py   Box-counting demo variant (esc-infer)
  recorder.py    Box-counting recorder (esc-record)
  dataset.py     Fine-tuning dataset capture/label/export (esc-dataset)
  finetune.py    Fine-tuning runner (esc-finetune)
  simulate.py    Box-counting simulator (esc-simulate)
  server.py      Box-counting dashboard (esc-server)

public/
  index.html, app.js   Box-counting dashboard
  edge/                Edge-ISR dashboard (stage-scale, OPS dark + ARCH light themed)

jobs/
  sensor-north-job.yaml           Sensor (north) — YOLO + Gemini + emitter
  sensor-south-job.yaml           Sensor (south)
  orchestrator-job.yaml           Local fusion node (FastAPI)
  armyx-tech-event-archive.yaml   S3 archive pipeline (Bloblang) — Beat 5 centerpiece
  event-archive-job.yaml          Generic local-only archive (kept for reference)
  yolo-detector-job.yaml          Box-counting variant
  security-camera-events-job.yaml Box-counting event enrichment

scripts/
  setup_jetson_lan.sh             On Jetson: dnsmasq on eth0 + NOPASSWD nmcli
  install_jetson_update_timer.sh  Jetson: nightly expanso-edge update timer
  jetson_update_expanso_edge.sh   The updater the timer runs
  rollback_engine.sh              Restore previous TensorRT engine
  detect_loop.py                  Box-counting GPU loop
  export_tensorrt.py              TensorRT engine builder
  ingest_drone_video.py           Capture frames from a video for fine-tuning
  ingest_3class.py / export_3class.py / claude_relabel_drones.py
                                  3-class (person/backpack/drone) dataset pipeline
  relabel_video.py                Re-detect a recorded MP4 with GroundingDINO
  run_sensor.sh                   Local sensor wrapper

internal/                         (gitignored — demo scripts, runbooks, bootstrap)
  DEMO_SCRIPT.md                  3:00 verbal demo script for recording
  DEMO_SCRIPT_1MIN.md             1:00 cold-intro version
  STAGE_RUNBOOK.md                Printable operator cheat sheet
  HACKATHON_SCRIPT.md             Implementation reference
  DEMO_UI_SPEC.md                 Dashboard design system
  PLAN.md / OPENSPEC.md           Planning docs
  scripts/bootstrap_armyx_tech.sh Day-1 cluster bootstrap
  scripts/teardown_armyx_tech.sh  Opt-in destructive teardown
  scripts/demo_reset.sh           One-shot Beat-0 lights-up reset
  scripts/demo_deploy_all.sh      Initial venue deploy
  scripts/precheck.sh             30-min-before-stage pass/fail
  jobs/armyx-tech-event-archive.yaml   The deployed pipeline spec
  jetson-systemd/                 Cluster-specific systemd units
```

---

## Configuration

`config.yaml` (gitignored — copy from `config.example.yaml`) drives the box-counting `esc-infer` variant. The Edge-ISR demo reads camera/cluster credentials from `.env`:

```bash
export CAM_USER=admin
export CAM_PASS_OUTSIDE=...        export CAM_PASS_INSIDE=...
export CAM_IP_OUTSIDE=192.168.2.10 export CAM_IP_INSIDE=192.168.2.11   # the actual deployed IPs
export ARMYX_JETSON_HOST=jetson    # for F1/F2 SSH actions
export GEMINI_API_KEY=...
```

Cameras live on the wired LAN (`enP8p1s0`, 192.168.2.x via PoE switch). The Jetson's WiFi (`wlP1p1s0`) is used only for WAN egress. F1 (kill WiFi) does not touch the cameras.

---

## Development

```bash
uv sync --extra test
uv run ruff check . --fix
uv run ruff format .
uv run pytest                                          # 120+ tests
uv run pytest tests/test_counter.py::test_name         # one test
```

For Claude Code or any AI assistant working in this repo: read [`CLAUDE.md`](./CLAUDE.md) first.
