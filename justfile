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
sensor_pid := state_dir / "sensor.pid"
go2rtc_pid := state_dir / "go2rtc.pid"
orch_log   := state_dir / "orchestrator.log"
sensor_log := state_dir / "sensor.log"
go2rtc_log := state_dir / "go2rtc.log"
port       := "8080"

go2rtc_bin    := "bin/go2rtc"
go2rtc_config := "go2rtc.yaml"
go2rtc_port   := "1984"
go2rtc_version := "1.9.14"

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

# start orchestrator + fake-multi sensor + go2rtc; wait for events
up: install-go2rtc
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p {{state_dir}}
    if [[ -f {{orch_pid}} ]] && kill -0 "$(cat {{orch_pid}})" 2>/dev/null; then
      echo "orchestrator already running (PID $(cat {{orch_pid}})); run 'just down' first"
      exit 1
    fi
    echo "→ starting orchestrator on port {{port}}…"
    uv run edge-orchestrator --port {{port}} > {{orch_log}} 2>&1 &
    echo $! > {{orch_pid}}
    for _ in $(seq 1 15); do
      curl -fsS http://localhost:{{port}}/metrics > /dev/null 2>&1 && break
      sleep 1
    done
    curl -fsS http://localhost:{{port}}/metrics > /dev/null \
      || { echo "orchestrator failed to come up; see {{orch_log}}"; exit 1; }
    echo "→ starting fake-multi sensor…"
    uv run edge-sensor --fake --multi \
        --orchestrator http://localhost:{{port}} --cadence 0.6 \
        > {{sensor_log}} 2>&1 &
    echo $! > {{sensor_pid}}
    for _ in $(seq 1 15); do
      events=$(curl -fsS http://localhost:{{port}}/metrics 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total_events",0))' \
        2>/dev/null || echo 0)
      if [[ "$events" -gt 0 ]]; then break; fi
      sleep 1
    done
    kill -0 "$(cat {{sensor_pid}})" 2>/dev/null \
      || { echo "sensor failed; see {{sensor_log}}"; exit 1; }
    echo "→ resolving AnkerWork AVFoundation indices (macOS reorders these)…"
    ./scripts/render-go2rtc-yaml.sh
    echo "→ starting go2rtc on port {{go2rtc_port}} (webcam → WebRTC)…"
    ./{{go2rtc_bin}} -c {{go2rtc_config}} > {{go2rtc_log}} 2>&1 &
    echo $! > {{go2rtc_pid}}
    for _ in $(seq 1 10); do
      curl -fsS http://localhost:{{go2rtc_port}}/api/streams > /dev/null 2>&1 && break
      sleep 1
    done
    curl -fsS http://localhost:{{go2rtc_port}}/api/streams > /dev/null \
      || { echo "go2rtc failed; see {{go2rtc_log}}"; exit 1; }
    echo
    echo "  ✓ orchestrator PID $(cat {{orch_pid}})  log: {{orch_log}}"
    echo "  ✓ sensor PID       $(cat {{sensor_pid}})  log: {{sensor_log}}"
    echo "  ✓ go2rtc PID       $(cat {{go2rtc_pid}})  log: {{go2rtc_log}}"
    echo
    echo "  dashboard:  http://localhost:{{port}}"
    echo "  streams:    curl http://localhost:{{go2rtc_port}}/api/streams"
    echo "  metrics:    curl http://localhost:{{port}}/metrics"
    echo "  jobs panel: curl http://localhost:{{port}}/jobs"
    echo "  follow:     just logs"
    echo "  stop:       just down"
    echo
    echo "  First run: macOS may prompt your terminal for Camera permission."
    echo "  Grant it in System Settings → Privacy & Security → Camera."

# stop orchestrator + sensor + go2rtc; verify ports free
down:
    #!/usr/bin/env bash
    set -euo pipefail
    for f in {{sensor_pid}} {{orch_pid}} {{go2rtc_pid}}; do
      if [[ -f "$f" ]]; then
        pid=$(cat "$f")
        if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null && echo "  killed $pid"; fi
        rm -f "$f"
      fi
    done
    # go2rtc spawns child ffmpeg procs that don't always die with the parent.
    # Kill any lingering ones to avoid camera-busy errors on the next 'just up'.
    pkill -f "{{go2rtc_bin}}" 2>/dev/null || true
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
    for f in {{orch_log}} {{sensor_log}} {{go2rtc_log}}; do
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
    for label in orchestrator sensor go2rtc; do
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
