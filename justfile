# Edge-ISR demo orchestration.
#
# Two flows:
#   - Laptop fake-multi (no GPU, no cameras):  up / down / clean / logs / status
#   - Jetson Expanso deploy:                   deploy / undeploy / redeploy / deploy-status
# Plus:                                        bump-sha  (pin all three YAMLs to origin/main)
#
# Run `just --list` (or just `just`) to list targets.

set shell := ["bash", "-euo", "pipefail", "-c"]

state_dir  := ".demo-state"
orch_pid   := state_dir / "orchestrator.pid"
sensor_north_pid := state_dir / "sensor-north.pid"
sensor_south_pid := state_dir / "sensor-south.pid"
go2rtc_pid := state_dir / "go2rtc.pid"
orch_log   := state_dir / "orchestrator.log"
sensor_north_log := state_dir / "sensor-north.log"
sensor_south_log := state_dir / "sensor-south.log"
go2rtc_log := state_dir / "go2rtc.log"
port       := "8080"

go2rtc_bin    := "bin/go2rtc"
go2rtc_config := "go2rtc.yaml"
go2rtc_port   := "1984"
go2rtc_rtsp_port := "8554"
go2rtc_version := "1.9.14"

# Expanso Edge daemon — registers the laptop as a node in the cloud
# control plane so jobs can be deployed/started from cloud.expanso.io.
# Data dir is local to the repo (gitignored under .demo-state/) instead
# of ~/.expanso-edge so each project is self-contained.
edge_data := state_dir / "expanso-edge"
edge_creds := edge_data / "auth/credentials.creds"
edge_pid := state_dir / "expanso-edge.pid"
edge_log := state_dir / "expanso-edge.log"

# Fine-tuned drone+person+backpack model (3-class). Thresholds for this
# specific weight file are tuned in detector.py (commit 283902b). Override
# with `YOLO_MODEL=path/to/other.pt just up` if needed.
yolo_model := env_var_or_default("YOLO_MODEL", "/Users/daaronch/drone-3class-v2.pt")

job_files := "jobs/orchestrator-job.yaml jobs/sensor-north-job.yaml jobs/sensor-south-job.yaml"
job_names := "fusion-node sensor-north sensor-south"

# default: list recipes
default:
    @just --list

# ── laptop fake-multi flow ─────────────────────────────────────────────────

# download go2rtc binary into bin/ if missing (idempotent)
install-go2rtc:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -x {{go2rtc_bin}} ]]; then
      echo "  ✓ {{go2rtc_bin}} already installed ($({{go2rtc_bin}} --version 2>&1 | head -1))"
      exit 0
    fi
    mkdir -p bin
    arch=$(uname -m)
    case "$arch" in
      arm64)  asset="go2rtc_mac_arm64.zip" ;;
      x86_64) asset="go2rtc_mac_amd64.zip" ;;
      *) echo "unsupported arch: $arch"; exit 1 ;;
    esac
    url="https://github.com/AlexxIT/go2rtc/releases/download/v{{go2rtc_version}}/${asset}"
    echo "→ downloading $url"
    curl -fL -o /tmp/go2rtc.zip "$url"
    unzip -o /tmp/go2rtc.zip -d bin/ > /dev/null
    chmod +x {{go2rtc_bin}}
    echo "  ✓ installed {{go2rtc_bin}} ($({{go2rtc_bin}} --version 2>&1 | head -1))"

# start orchestrator + go2rtc + two REAL edge-sensors doing YOLO on
# each Anker stream. No fake events. Also bootstraps + runs the Expanso
# Edge daemon so the laptop appears as a node in cloud.expanso.io and
# jobs can be deployed from the cloud control plane.
up: install-go2rtc
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p {{state_dir}}
    if [[ -f {{orch_pid}} ]] && kill -0 "$(cat {{orch_pid}})" 2>/dev/null; then
      echo "orchestrator already running (PID $(cat {{orch_pid}})); run 'just down' first"
      exit 1
    fi
    # Load .env so EXPANSO_EDGE_BOOTSTRAP_TOKEN + ARMYX_* land in our
    # subshell. Python entrypoints load_dotenv separately (orchestrator,
    # sensor); expanso-edge needs the var exported BEFORE the binary
    # starts because there's no dotenv hook in Go.
    if [[ -f .env ]]; then set -a; source .env; set +a; fi
    # Bootstrap expanso-edge ONCE — idempotent guard on the creds file.
    if [[ ! -f {{edge_creds}} ]]; then
      echo "→ bootstrapping expanso-edge to cloud.expanso.io (one-time)…"
      mkdir -p {{edge_data}}
      expanso-edge bootstrap --data-dir {{edge_data}} \
        > {{edge_log}} 2>&1 \
        || { echo "expanso-edge bootstrap failed; see {{edge_log}}"; exit 1; }
    fi
    # Refresh node labels every just up (idempotent overwrite) — they're
    # what the per-platform job selectors target.
    mkdir -p {{edge_data}}/config.d
    cp scripts/expanso-edge-labels.yaml {{edge_data}}/config.d/20-labels.yaml
    echo "→ starting expanso-edge daemon (cloud control plane connection)…"
    nohup expanso-edge run --data-dir {{edge_data}} >> {{edge_log}} 2>&1 &
    echo $! > {{edge_pid}}
    echo "→ starting orchestrator on port {{port}}…"
    uv run edge-orchestrator --port {{port}} > {{orch_log}} 2>&1 &
    echo $! > {{orch_pid}}
    for _ in $(seq 1 15); do
      curl -fsS http://localhost:{{port}}/metrics > /dev/null 2>&1 && break
      sleep 1
    done
    curl -fsS http://localhost:{{port}}/metrics > /dev/null \
      || { echo "orchestrator failed to come up; see {{orch_log}}"; exit 1; }
    echo "→ resolving AnkerWork AVFoundation indices…"
    ./scripts/render-go2rtc-yaml.sh
    echo "→ starting go2rtc (WebRTC on :{{go2rtc_port}}, RTSP on :{{go2rtc_rtsp_port}})…"
    ./{{go2rtc_bin}} -c {{go2rtc_config}} > {{go2rtc_log}} 2>&1 &
    echo $! > {{go2rtc_pid}}
    for _ in $(seq 1 10); do
      curl -fsS http://localhost:{{go2rtc_port}}/api/streams > /dev/null 2>&1 && break
      sleep 1
    done
    curl -fsS http://localhost:{{go2rtc_port}}/api/streams > /dev/null \
      || { echo "go2rtc failed; see {{go2rtc_log}}"; exit 1; }
    # Wait until go2rtc has actually opened the Ankers (consumer-on-demand).
    # We trigger that by pulling one snapshot frame from each stream — this
    # forces ffmpeg to start, which unblocks the downstream RTSP pulls.
    echo "→ priming both Anker streams (forces ffmpeg start)…"
    curl -fsS -o /dev/null --max-time 10 "http://localhost:{{go2rtc_port}}/api/frame.jpeg?src=cam-outside"
    curl -fsS -o /dev/null --max-time 10 "http://localhost:{{go2rtc_port}}/api/frame.jpeg?src=cam-inside"
    echo
    echo "  ✓ expanso-edge  PID $(cat {{edge_pid}})        log: {{edge_log}}"
    echo "  ✓ orchestrator  PID $(cat {{orch_pid}})         log: {{orch_log}}"
    echo "  ✓ go2rtc        PID $(cat {{go2rtc_pid}})       log: {{go2rtc_log}}"
    echo
    echo "  dashboard:  http://localhost:{{port}}    (cameras streaming, NO detection yet)"
    echo "  cloud node: expanso-cli node list           (Mac registered as M5-Max in armyx-tech)"
    echo
    echo "  ── Next: turn on detection from Expanso Cloud ──"
    echo "    just detect-on        # deploys + starts sensor-north + sensor-south as cluster jobs"
    echo "    just detect-off       # stops them (cameras keep streaming)"
    echo
    echo "  follow: just logs    stop: just down"
    echo "  First run: macOS may prompt your terminal for Camera permission."

# stop orchestrator + sensors + go2rtc; verify ports free
down:
    #!/usr/bin/env bash
    set -euo pipefail
    for f in {{sensor_north_pid}} {{sensor_south_pid}} {{orch_pid}} {{go2rtc_pid}} {{edge_pid}}; do
      if [[ -f "$f" ]]; then
        pid=$(cat "$f")
        if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null && echo "  killed $pid"; fi
        rm -f "$f"
      fi
    done
    # Belt-and-suspenders: kill anything matching our process patterns that
    # outlived its pidfile (fake sensors started in a prior session,
    # orchestrator restarted manually, etc.). Without this, stale `--fake`
    # sensors keep emitting hardcoded events alongside the real ones and the
    # dashboard mixes the two.
    pkill -f "edge-sensor" 2>/dev/null || true
    pkill -f "edge-orchestrator" 2>/dev/null || true
    pkill -f "{{go2rtc_bin}}" 2>/dev/null || true
    pkill -f "expanso-edge run" 2>/dev/null || true
    sleep 1
    failed=0
    for p in {{port}} {{go2rtc_port}}; do
      if lsof -nP -iTCP:$p -sTCP:LISTEN > /dev/null 2>&1; then
        echo "  ⚠ port $p still listening"
        lsof -nP -iTCP:$p -sTCP:LISTEN
        failed=1
      else
        echo "  ✓ port $p free"
      fi
    done
    exit $failed

# stop + remove transient state (db, ndjson, logs)
clean: down
    @rm -f orchestrator.db events.ndjson sensor-north.db sensor-south.db
    @rm -rf {{state_dir}}
    @echo "  ✓ removed: orchestrator.db, events.ndjson, sensor-*.db, {{state_dir}}/"

# tail all logs interleaved
logs:
    #!/usr/bin/env bash
    set -euo pipefail
    files=()
    for f in {{orch_log}} {{sensor_north_log}} {{sensor_south_log}} {{go2rtc_log}} {{edge_log}}; do
      [[ -f "$f" ]] && files+=("$f")
    done
    if [[ ${#files[@]} -eq 0 ]]; then
      echo "no logs; run 'just up' first"
      exit 1
    fi
    tail -F "${files[@]}"

# show process + metrics state
status:
    #!/usr/bin/env bash
    set -euo pipefail
    for label in orchestrator sensor-north sensor-south go2rtc expanso-edge; do
      pidfile={{state_dir}}/$label.pid
      if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "  $label: running (PID $(cat "$pidfile"))"
      else
        echo "  $label: not running"
      fi
    done
    echo
    curl -fsS http://localhost:{{port}}/metrics 2>/dev/null \
      | python3 -m json.tool 2>/dev/null \
      || echo "  (no /metrics reachable)"
    echo
    streams=$(curl -fsS http://localhost:{{go2rtc_port}}/api/streams 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(f"  go2rtc stream: {k}") for k in d]' \
      2>/dev/null) && echo "$streams" || echo "  (no go2rtc reachable)"

# ── Cloud-driven detection (the "Expanso Cloud turns it on" step) ─────────

# Deploy + start sensor-north & sensor-south as cluster jobs. The selector
# `site: laptop-demo` in each YAML pins them to this Mac. The cluster
# control plane dispatches them; this laptop's expanso-edge runs the
# subprocess (uv run --from git+...@SHA → edge-sensor), which pulls
# frames from go2rtc's RTSP listener and POSTs events to the local
# orchestrator. That's the "go to Expanso Cloud, turn on the detector"
# step of the demo flow, scripted.
detect-on:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v expanso-cli > /dev/null \
      || { echo "expanso-cli not on PATH"; exit 1; }
    # Stop any existing executions first. Expanso doesn't have a
    # `job start` verb — deploy auto-rolls out. But if the previous
    # version's executions are still running, they keep their old spec
    # (paths, selector) until killed. Stop first, deploy second.
    for n in sensor-north sensor-south; do
      echo "→ stopping any existing $n execution"
      expanso-cli job stop --force "$n" 2>/dev/null || true
    done
    # Brief settle so the scheduler clears the old eval before we deploy
    sleep 2
    for f in jobs/sensor-north-job.yaml jobs/sensor-south-job.yaml; do
      echo "→ deploying $f (auto-starts via rollout)"
      expanso-cli job deploy --force "$f"
    done
    echo
    echo "  Watch detection start: just logs    (look for [sensor-*] yolo: PASS lines)"
    echo "  Stop:                  just detect-off"

# Stop both sensor jobs in the cluster — cameras + dashboard keep running,
# but no events are emitted. This is what step 4 of the demo ('disable
# cloud connection') currently looks like; once the WAN-down work lands,
# F1 will simulate this more naturally.
detect-off:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v expanso-cli > /dev/null \
      || { echo "expanso-cli not on PATH"; exit 1; }
    for n in sensor-north sensor-south; do
      echo "→ stopping $n"
      expanso-cli job stop --force "$n" 2>/dev/null || echo "  (was not running)"
    done

# ── Jetson Expanso deploy ──────────────────────────────────────────────────

# deploy + start all three Expanso jobs
deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v expanso-cli > /dev/null || { echo "expanso-cli not on PATH"; exit 1; }
    for f in {{job_files}}; do
      echo "→ deploying $f"
      expanso-cli job deploy "$f"
    done
    for n in {{job_names}}; do
      echo "→ starting $n"
      expanso-cli job start "$n"
    done
    echo "  ✓ deployed. status: just deploy-status"

# stop + remove all three Expanso jobs
undeploy:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v expanso-cli > /dev/null || { echo "expanso-cli not on PATH"; exit 1; }
    for n in {{job_names}}; do
      echo "→ stopping $n"
      expanso-cli job stop "$n" 2>/dev/null || echo "  (not running)"
    done
    for n in {{job_names}}; do
      echo "→ removing $n"
      expanso-cli job rm "$n" 2>/dev/null || echo "  (not found)"
    done

# undeploy then deploy (atomic version bump)
redeploy: undeploy deploy

# show cluster state for the three jobs (+ event-archive)
deploy-status:
    @command -v expanso-cli > /dev/null || { echo "expanso-cli not on PATH"; exit 1; }
    @expanso-cli job list | awk 'NR==1 || /fusion-node|sensor-north|sensor-south|event-archive/'

# ── SHA bump (atomic across all three YAMLs) ───────────────────────────────

# pin all three YAMLs to origin/main HEAD
bump-sha:
    #!/usr/bin/env bash
    set -euo pipefail
    git fetch origin main --quiet
    sha=$(git rev-parse origin/main)
    echo "→ pinning to $sha"
    for f in {{job_files}}; do
      sed -i.bak -E "s|(expanso-edge-video-and-event-detection)@[0-9a-f]{40}|\1@$sha|g" "$f"
      rm -f "$f.bak"
    done
    git --no-pager diff --stat {{job_files}}
