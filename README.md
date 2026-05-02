# Edge ISR Demo · Expanso

Edge inference + cloud reachback + cross-sensor fusion + DDIL graceful degradation.
Two cameras, a Jetson, and a laptop, all running as Expanso jobs.

This repo holds two demos:

1. **Edge ISR** — the headline 4-minute Army RFI demo. YOLO + Gemini cascade on the Jetson, FastAPI/WebSocket orchestrator on the laptop, full-screen stage-scale dashboard.
   - Spec: [`HACKATHON_SCRIPT.md`](./HACKATHON_SCRIPT.md)
   - Stage runbook: [`STAGE_RUNBOOK.md`](./STAGE_RUNBOOK.md)
   - UI design system: [`DEMO_UI_SPEC.md`](./DEMO_UI_SPEC.md)
2. **Box counting** — the original warehouse box-transfer demo, kept intact alongside.
   - See `src/expanso_security_camera/inference.py` and the `esc-*` CLI scripts.

---

## Run the Edge ISR demo on a laptop in 60 seconds

No GPU, no cameras, no Jetson required — uses synthetic events and procedurally generated camera feeds.

```bash
uv sync                                                # one-time

# Terminal 1
uv run edge-orchestrator --port 8080

# Terminal 2
uv run edge-sensor --fake --multi --orchestrator http://localhost:8080 --cadence 0.6
```

Open `http://localhost:8080` in a browser, hit F11 for fullscreen, and you have the full demo loop:

- Two live sector camera tiles (synthesized with bounding boxes)
- Live event stream per sector with DBOM signatures
- Trigger bar at the top showing active classes
- EXPANSO PLATFORM tile showing 4 jobs running
- Footer with `events/min`, total events, signed count, fused count

**Operator shortcuts** (keyboard focus on dashboard):
- `F1` — Jetson WAN DOWN (real, via SSH `nmcli radio wifi off`; falls back to cosmetic flag in laptop-dev mode)
- `F2` — Jetson WAN UP
- `F3` — fire a synthetic fused alert (full-screen takeover)
- `F4` — push the live trigger update that adds `drone` to the watch list

---

## On-stage architecture (armyx-tech cluster)

```
   Mac's Wi-Fi (your internet, kept up)              Jetson's Wi-Fi (toggleable WAN)
            │                                                       │
   ┌────────┴─────────┐                              ┌──────────────┴───────┐
   │  AWS S3 (boto3)  │                              │ cloud.expanso.io     │
   │  + console tab   │                              │ pipeline control     │
   └────────┬─────────┘                              └──────────┬───────────┘
            │                                                   │
            ▼                                                   ▼
       ┌────────┐  USB-C eth   ┌─────────────┐   eth0   ┌─────────┐
       │  Mac   │──────────────│ PoE+ switch │──────────│ Jetson  │
       │ wlan0  │              └─┬─────────┬─┘          │  wlan0  │ ← F1/F2 toggles this
       └────────┘                ▼         ▼            └─────────┘
       192.168.50.30          Reolink   Reolink         192.168.50.1
       (fusion node + UI)     .50.11    .50.12          (DHCP+DNS host)
```

Every component runs as an Expanso job (`jobs/*.yaml`). `expanso-cli job list` shows four jobs on the `armyx-tech` cluster: `fusion-node`, `sensor-north`, `sensor-south`, `armyx-tech-event-archive`. ("Orchestrator" in this codebase means **Expanso Cloud**, the cluster control plane — not the local FastAPI process. The local FastAPI process is the `fusion-node` job: event store + cross-sensor correlator + dashboard backend.)

The `armyx-tech-event-archive` pipeline writes every signed event to a real S3 bucket with date-partitioned keys; the dashboard's "Cloud egress" tile polls the bucket from the Mac (independent path) so judges see data move in real time and verify objects via the in-dashboard JSON viewer.

---

## Day-1 bootstrap (AWS + creds + pipeline deploy)

```bash
JETSON_HOST=nvidia@jetson.local \
  ARMYX_EXPANSO_ENDPOINT=https://<your-cluster>.cloud.expanso.io:9010 \
  ARMYX_EXPANSO_API_KEY=exp_ak_... \
  ./scripts/bootstrap_armyx_tech.sh

# On the Jetson, once:
sudo ./scripts/setup_jetson_lan.sh
```

Creates the S3 bucket, the scoped IAM user, distributes credentials to Mac and Jetson, registers the `armyx-tech` expanso-cli profile, and deploys the archive pipeline. Idempotent. See `STAGE_RUNBOOK.md` for the demo flow this enables.

---

## Build for the Jetson

```bash
# On the target Jetson, inside the L4T container:
python scripts/export_tensorrt.py            # produces yolo11s.engine

# Back on dev machine, with yolo11s.engine next to Dockerfile.sensor:
docker build -f Dockerfile.sensor -t ghcr.io/aronchick/edge-isr-sensor:demo .
docker push ghcr.io/aronchick/edge-isr-sensor:demo

# Deploy (the bootstrap script does this for you, but here's the manual path):
expanso-cli profile select armyx-tech
expanso-cli job deploy jobs/fusion-node-job.yaml
expanso-cli job deploy jobs/sensor-north-job.yaml
expanso-cli job deploy jobs/sensor-south-job.yaml
expanso-cli job deploy jobs/armyx-tech-event-archive.yaml

# Verify:
expanso-cli job list
scripts/precheck.sh
```

---

## Repo layout

```
src/expanso_security_camera/
  sensor/        Edge-ISR sensor: pipeline, detector, emitter, dbom, main, triggers_client
  orchestrator/  FastAPI + WS, store, correlator, metrics, jobs_status, snapshots, triggers
  inference.py   Box-counting demo (esc-infer)
  recorder.py    Box-counting recorder (esc-record)
  dataset.py     Fine-tuning dataset capture/label/export (esc-dataset)
  finetune.py    Fine-tuning runner (esc-finetune)
  simulate.py    Box-counting simulator (esc-simulate)
  server.py      Box-counting dashboard (esc-server)

public/
  index.html, app.js   Box-counting dashboard
  edge/                Edge-ISR dashboard (stage-scale)

jobs/
  yolo-detector-job.yaml          Box-counting GPU loop
  security-camera-events-job.yaml Box-counting event enrichment
  fusion-node-job.yaml            Edge-ISR fusion node (laptop FastAPI backend; renamed from orchestrator-job.yaml)
  sensor-north-job.yaml           Edge-ISR sensor (north)
  sensor-south-job.yaml           Edge-ISR sensor (south)
  armyx-tech-event-archive.yaml   Edge-ISR S3 archive pipeline (Beat 5 centerpiece)
  event-archive-job.yaml          Legacy local-only archive (kept for ref; superseded)

scripts/
  bootstrap_armyx_tech.sh         Day-1 AWS+creds+pipeline bootstrap
  teardown_armyx_tech.sh          Tear down everything Project=armyx-tech (opt-in destructive)
  setup_jetson_lan.sh             On Jetson: dnsmasq on eth0 + NOPASSWD nmcli
  detect_loop.py                  Box-counting GPU detection loop
  export_tensorrt.py              Edge-ISR TensorRT engine builder
  precheck.sh                     Pre-stage pass/fail script
  esc-server.service              systemd unit for box-counting dashboard

Dockerfile.jetson    Box-counting (Ultralytics base)
Dockerfile.sensor    Edge-ISR (L4T PyTorch base + GStreamer OpenCV)
```

---

## Development

```bash
uv sync --extra test
uv run ruff check . --fix
uv run ruff format .
uv run pytest                                          # 122 tests
uv run pytest tests/test_counter.py::test_name         # one test
```

For Claude Code or any AI assistant working in this repo: read [`CLAUDE.md`](./CLAUDE.md) first.
