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
orch_log   := state_dir / "orchestrator.log"
sensor_log := state_dir / "sensor.log"
port       := "8080"

job_files := "jobs/orchestrator-job.yaml jobs/sensor-north-job.yaml jobs/sensor-south-job.yaml"
job_names := "fusion-node sensor-north sensor-south"

# default: list recipes
default:
    @just --list

# ── laptop fake-multi flow ─────────────────────────────────────────────────

# start orchestrator + fake-multi sensor; wait for events
up:
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
    echo
    echo "  ✓ orchestrator PID $(cat {{orch_pid}})  log: {{orch_log}}"
    echo "  ✓ sensor PID       $(cat {{sensor_pid}})  log: {{sensor_log}}"
    echo
    echo "  dashboard:  http://localhost:{{port}}"
    echo "  metrics:    curl http://localhost:{{port}}/metrics"
    echo "  jobs panel: curl http://localhost:{{port}}/jobs"
    echo "  follow:     just logs"
    echo "  stop:       just down"

# stop orchestrator + sensor; verify port free
down:
    #!/usr/bin/env bash
    set -euo pipefail
    for f in {{sensor_pid}} {{orch_pid}}; do
      if [[ -f "$f" ]]; then
        pid=$(cat "$f")
        if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null && echo "  killed $pid"; fi
        rm -f "$f"
      fi
    done
    for _ in $(seq 1 5); do
      if ! lsof -nP -iTCP:{{port}} -sTCP:LISTEN > /dev/null 2>&1; then
        echo "  ✓ port {{port}} free"
        exit 0
      fi
      sleep 1
    done
    echo "  ⚠ port {{port}} still listening after teardown"
    lsof -nP -iTCP:{{port}} -sTCP:LISTEN
    exit 1

# stop + remove transient state (db, ndjson, logs)
clean: down
    @rm -f orchestrator.db events.ndjson sensor-north.db sensor-south.db
    @rm -rf {{state_dir}}
    @echo "  ✓ removed: orchestrator.db, events.ndjson, sensor-*.db, {{state_dir}}/"

# tail orchestrator + sensor logs interleaved
logs:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! -f {{orch_log}} || ! -f {{sensor_log}} ]]; then
      echo "no logs; run 'just up' first"
      exit 1
    fi
    tail -F {{orch_log}} {{sensor_log}}

# show process + metrics state
status:
    #!/usr/bin/env bash
    set -euo pipefail
    for label in orchestrator sensor; do
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
