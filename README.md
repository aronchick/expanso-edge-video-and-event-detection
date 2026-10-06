# Expanso Edge: video and event detection

Edge sensors usually ship every frame to the cloud and wait for someone to decide whether it mattered. That costs bandwidth on a contested link, latency on every decision, emissions an adversary can detect, and a single point of failure.

This repository is a working reference for moving the workload to the data. A local YOLO detector counts people in each camera, a fusion node merges the counts into one combined number and flags a crowd, an optional analyst summary comes through the demo-kit model gateway, and every event is signed and archived. It runs as nine [Expanso Edge](https://expanso.io) pipelines, and each one is proven on real recorded data.

**Start with the guide.** It has the explanation, a step explorer that shows the real input and output of every stage of every pipeline, and the run and deploy instructions. Serve it with `uv run edge-orchestrator` and open <http://localhost:8080/guide/>, or run `just guide` and open <http://127.0.0.1:18281/guide/> (Ctrl-C, or `just guide-down` from another terminal, stops it). The page is generated: `public/guide/index.html`.

## What it delivers

1. **Detection stays local.** YOLO runs on the node. Nothing leaves it for the node to know what it is looking at.
2. **The merge happens at the edge.** The fusion node adds the per-zone people counts into one combined count. The crowd flag trips when the combined count is at least 3 (`EDGE_CROWD_THRESHOLD`), every zone has at least one person, and the busiest has at least two. North 2 and south 1 flag; north 4 and south 0 does not.
3. **An analyst summary is optional.** When a detection warrants it the sensor asks the demo model gateway for one sentence. Rehearsals replay recorded answers (`fixtures/model/`); no model is called.
4. **The mission updates live.** Arm a class (F4 adds `backpack` and `drone`) and every sensor picks it up within about a second, with no restart.
5. **The link can drop.** Sensors keep detecting and the dashboard keeps painting. The sensor queues events in SQLite and replays them when the orchestrator returns; Expanso Edge buffers archive writes until the WAN is back.

## The pipelines

| Job | What runs in it |
|---|---|
| `sensor-north`, `sensor-south` | `edge-sensor` on a camera. Prints one signed JSON event per frame. |
| `fusion-node` | `edge-orchestrator`: the people-count merge and the dashboard backend. Prints a tally line per event and an alert line on a flag. |
| `fuse` | The cross-zone merge as its own pipeline (`scripts/fuse-correlator.py`). Signs each crowd alert. |
| `event-archive` | Follows the event log, checks signatures, writes to S3, a daily file and the job log. |
| `yolo-detector` | Box counting: the GPU loop on a Jetson. |
| `security-camera-events` | Box counting: validates and enriches the detection scans. |
| `box-crossing` | Box counting: `esc-infer` line crossings and the arrivals-versus-departures reconciliation. |
| `security-camera-recorder` | Box counting: segmented video evidence. |

Every job is in `jobs/`. Each processor is labelled, so Expanso Cloud shows named stages, and each job logs one line per event so the Logs page is live.

## Run it on a laptop

No GPU, camera or model weights. The two recorded scenes are real YOLO measurements (`fixtures/scenes/`). For synthetic crowds in one command, run `just fake`; Ctrl-C stops everything it started.

```bash
uv sync

# Terminal 1: the fusion node, dashboard and guide
uv run edge-orchestrator --port 8080

# Terminals 2 and 3: replay the recorded scenes
uv run edge-sensor --replay fixtures/scenes/north.jsonl \
  --node-id sensor-north --orchestrator http://localhost:8080 --speed 2
uv run edge-sensor --replay fixtures/scenes/south.jsonl \
  --node-id sensor-south --orchestrator http://localhost:8080 --speed 2
```

`edge-sensor --fake --multi` plays random crowds instead. With two USB webcams, `uv run --extra vision edge-sensor --cameras 0,1`.

| Key | Action |
|---|---|
| `F1` | Take the cloud link down. The banner appears within about 3 s. |
| `F2` | Restore the link; buffered events drain to the archive. |
| `F3` | Rehearse the crowd flag (6 people, 3+3). It holds for 6 s. |
| `F4` | Arm `backpack` and `drone` live. |

## Run it through Expanso Edge

On the booth Mac with two webcams, `just up` deploys or updates the Cloud jobs,
then starts the Expanso Edge node, fusion node and dashboard, go2rtc, and camera
capture. `just down` stops and cleans up all of it and checks that its ports are
free.

```bash
just up
just down
```

Capture, merge and archive run as Expanso pipeline jobs deployed from `jobs/`. Prepare the node, set the `EDGE_ISR_*` variables the jobs read (the guide's Deploy section lists them), then:

```bash
for job in orchestrator sensor-north sensor-south fuse event-archive; do
  expanso-cli job deploy jobs/$job-job.yaml
done
expanso-cli job list
```

The archive writes to S3 over HTTPS with the standard AWS credential chain. It needs only `s3:PutObject` on `events/*` of the bucket. Box counting deploys the same way (`yolo-detector`, `security-camera-events`, `box-crossing`, `recorder`); its GPU detector runs in the pinned Ultralytics Jetson image with the NVIDIA runtime.

The fusion node's API has no login. It listens on `127.0.0.1` unless `EDGE_ISR_BIND` says otherwise; open it to a LAN only on an isolated network.

## How it is proven

| Claim | Proof | Command |
|---|---|---|
| Every pipeline validates and produces its expected output on real input | `fixtures/<pipeline>/` holds the input, the expected output and, per stage, the input and output, all produced by Expanso Edge v2.1.21 | `uv run -s scripts/build-fixtures.py` |
| The five public-bar criteria hold | `docs/PROOF.md` | `uv run -s .demo-kit/public-bar.py --repo . --manifest public-bar.toml --lane all` |
| The archive writes to S3 over TLS with least-privilege credentials | `docs/s3-proof.json` | `uv run -s scripts/prove-s3.py` |
| The guide matches the fixtures | CI | `uv run -s scripts/build-guide.py --check` |

`scripts/edge-replay.py` runs one job over one input in a throwaway Expanso Edge container (`proof/Dockerfile`), so no host Expanso configuration is touched.

## Architecture

```
                         Expanso Cloud: jobs and config only
                                       |
   camera north --RTSP--> sensor-north --\                      /--> event-archive --> S3
                                          >--> fusion-node ----+--> fuse (signed alerts)
   camera south --RTSP--> sensor-south --/       (merge)        \--> dashboard (WebSocket)
```

The link to Expanso Cloud is the control plane (job specs and trigger config). Frames, events and S3 objects are the data plane and never pass through Expanso Cloud.

## Repository layout

```
jobs/                  the nine Expanso Edge jobs
fixtures/              recorded scenes, producer output, and per-pipeline, per-stage fixtures
public/guide/          the guide (generated), public/edge/ the dashboard, public/*.html box pages
scripts/               record-scene, record-fixtures, build-fixtures, build-guide, edge-replay,
                       prove-s3, tail-ndjson, fuse-correlator, detect_loop, Jetson setup scripts
src/expanso_security_camera/
  sensor/              edge-sensor: cascade, detector, replay, emitter, dbom
  orchestrator/        edge-orchestrator: API, zones (the merge), store, S3 watcher
proof/                 runner images for the replay, the S3 proof and the public-bar check
public-bar.toml        the public-bar manifest; public-features.json lists retained features
```

## Configuration

`config.yaml` (gitignored; copy `config.example.yaml`) drives `esc-infer` for box counting. Camera credentials come from the environment (`CAM_USER`, `CAM_PASS_*`, `CAM_IP_*`) and never from a committed file. The Edge ISR jobs read `EDGE_ISR_*` variables from the node; the guide lists them.

## Development

```bash
uv sync --extra test
uv run ruff check . && uv run ruff format --check .
uv run pytest
```
