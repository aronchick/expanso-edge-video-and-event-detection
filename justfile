# Edge-ISR demo orchestration.
#
# Three flows (everything starts/stops together — no loose processes):
#   - Local test (no cameras, no cloud):   just fake     → just down
#   - Booth real (webcams + Expanso Edge): just up + just deploy → just down
#   - Job management:                      deploy / undeploy / redeploy / deploy-status
# Plus:  clean (reset state) · status · logs · bump-sha (pin YAMLs to origin/main)
#
# Run `just --list` (or just `just`) to list targets.

set shell := ["bash", "-euo", "pipefail", "-c"]

state_dir  := ".demo-state"
orch_pid   := state_dir / "orchestrator.pid"
sensor_north_pid := state_dir / "sensor-north.pid"
sensor_south_pid := state_dir / "sensor-south.pid"
go2rtc_pid := state_dir / "go2rtc.pid"
dualcam_pid := state_dir / "dual-cam.pid"
dualcam_log := state_dir / "dual-cam.log"
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
# expanso-edge's LOCAL node API. Kept off 9010 because the opt-in egress
# tunnel (scripts/tunnel-up.sh) binds 127.0.0.1:9010 to forward the CLOUD
# cluster API — with the tunnel up, an edge on its 9010 default dies with
# "bind: address already in use". Harmless on the default direct path, and
# staying on 9011 means turning the tunnel on never breaks the node.
# The node reaches the cloud orchestrator over NATS:4222 either way; this
# port is only the local introspection API.
edge_api_listen := "127.0.0.1:9011"

# Fine-tuned drone+person+backpack model (3-class). Thresholds for this
# specific weight file are tuned in detector.py (commit 283902b). Override
# with `YOLO_MODEL=path/to/other.pt just up` if needed.
yolo_model := env_var_or_default("YOLO_MODEL", "/Users/daaronch/drone-3class-v2.pt")

# The fuse pipeline (cross-zone people-count MERGE, visible in cloud.expanso.io)
# deploys + tears down with the rest so `just deploy` / `just undeploy` cover
# everything. Orchestrator first (it writes events.ndjson that fuse tails).
job_files := "jobs/orchestrator-job.yaml jobs/sensor-north-job.yaml jobs/sensor-south-job.yaml jobs/fuse-job.yaml"
job_names := "fusion-node sensor-north sensor-south fuse"

# default: list recipes
default:
    @just --list

# cams: show what AVFoundation cameras macOS exposes + which the demo would
# pick. Use this to debug enumeration (e.g. Ankers dropping off the USB bus)
# without parsing ffmpeg by hand.
cams:
    #!/usr/bin/env bash
    set -uo pipefail
    LIST=$(ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 || true)
    VIDEO_BLOCK=$(printf '%s\n' "$LIST" \
      | awk '/AVFoundation video devices/{p=1; next} /AVFoundation audio devices/{p=0} p')
    echo "AVFoundation video devices macOS sees right now:"
    if [[ -n "$VIDEO_BLOCK" ]]; then
      printf '%s\n' "$VIDEO_BLOCK" | sed -E 's/.*(\[[0-9]+\] .*)/  \1/'
    else
      echo "  (none — is ffmpeg installed? is anything plugged in?)"
    fi
    anker=$(printf '%s\n' "$VIDEO_BLOCK" | grep -icE '\[[0-9]+\][[:space:]].*anker' || true)
    brio=$(printf '%s\n'  "$VIDEO_BLOCK" | grep -icE '\[[0-9]+\][[:space:]].*brio' || true)
    mac=$(printf '%s\n'   "$VIDEO_BLOCK" | grep -iE '\[[0-9]+\][[:space:]].*macbook' \
      | grep -ivcE 'desk view' || true)
    echo
    echo "  AnkerWork cameras: $anker   |   Brio: $brio   |   MacBook built-in: $mac"
    echo
    if [[ "${anker:-0}" -ge 2 ]]; then
      echo "  ✓ READY — 'just up' will use the two AnkerWork cameras."
    else
      echo "  ✗ 'just up' (default) will WARN+QUIT — needs 2 AnkerWork cameras, found ${anker:-0}."
      if [[ "${brio:-0}" -ge 1 && "${mac:-0}" -ge 1 ]]; then
        echo "    → fallback ready:  CAMERA_TEST=true just up   (Brio + MacBook built-in)"
      else
        echo "    → no Brio+MacBook fallback either; reconnect cams or use"
        echo "      EDGE_CAM_OUTSIDE_IDX=<n> EDGE_CAM_INSIDE_IDX=<m> just up"
      fi
    fi

# ── local test: no cameras, no cloud ───────────────────────────────────────

# fake: one-command local test of the people-count MERGE + dashboard theme.
# Starts the orchestrator + synthetic crowd sensors (both zones in one
# process) and stays in the FOREGROUND tailing logs. Press Ctrl-C and a
# trap tears down EVERYTHING it started — nothing is left running in the
# background. No go2rtc, no Expanso cloud, no webcams.
fake:
    #!/usr/bin/env bash
    # NOTE: deliberately no `set -e` — this is a long-running foreground
    # session; we handle errors explicitly and clean up via trap.
    set -uo pipefail
    mkdir -p {{state_dir}}
    if lsof -nP -iTCP:{{port}} -sTCP:LISTEN > /dev/null 2>&1; then
      echo "port {{port}} already in use — run 'just down' first"; exit 1
    fi

    orch_pid=""; sensor_pid=""
    cleanup() {
      trap - INT TERM HUP EXIT          # disarm so cleanup runs exactly once
      echo
      echo "→ stopping everything this session started…"
      [[ -n "$sensor_pid" ]] && kill "$sensor_pid" 2>/dev/null
      [[ -n "$orch_pid"   ]] && kill "$orch_pid"   2>/dev/null
      # belt-and-suspenders for uv-run grandchildren that outlive the parent
      pkill -f "edge-sensor --fake" 2>/dev/null
      pkill -f "edge-orchestrator --port {{port}}" 2>/dev/null
      rm -f {{orch_pid}} {{sensor_north_pid}}
      echo "  ✓ orchestrator + sensors stopped"
    }
    # Ctrl-C / terminal close → clean up and exit 0 (a tidy stop, not an error).
    # Any other exit (e.g. failed startup) still runs cleanup via the EXIT trap
    # but preserves the real non-zero status.
    on_signal() { cleanup; exit 0; }
    trap on_signal INT TERM HUP
    trap cleanup EXIT

    echo "→ orchestrator on :{{port}} (fake mode, no cloud)…"
    uv run edge-orchestrator --port {{port}} > {{orch_log}} 2>&1 &
    orch_pid=$!
    echo "$orch_pid" > {{orch_pid}}
    for _ in $(seq 1 20); do
      curl -fsS "http://localhost:{{port}}/metrics" > /dev/null 2>&1 && break
      sleep 1
    done
    if ! curl -fsS "http://localhost:{{port}}/metrics" > /dev/null 2>&1; then
      echo "orchestrator failed to come up; see {{orch_log}}"; exit 1
    fi

    echo "→ synthetic crowd sensors (sensor-north + sensor-south)…"
    uv run edge-sensor --fake --multi --orchestrator "http://localhost:{{port}}" --cadence 1.2 \
      > {{sensor_north_log}} 2>&1 &
    sensor_pid=$!
    echo "$sensor_pid" > {{sensor_north_pid}}

    echo
    echo "  ✓ dashboard: http://localhost:{{port}}   (F11 full-screen)"
    echo "    counts tick per zone; combined > threshold → coral CROWD FLAG."
    echo "    F3 = rehearsal FLAG · F1/F2 = cloud down/up"
    echo
    echo "  ── running in the FOREGROUND · press Ctrl-C to stop EVERYTHING ──"
    echo
    # Hold the terminal open and stream both logs. Ctrl-C interrupts tail and
    # fires the cleanup trap, taking orchestrator + sensors down with it.
    tail -f {{orch_log}} {{sensor_north_log}}

# detect-local: run the two REAL sensors (real cameras + YOLO + the merge)
# locally against the go2rtc RTSP streams, WITHOUT the Expanso Cloud control
# plane. Use this when the cloud tunnel is flaky (booth reliability) — it's
# the same edge-sensor + YOLO + zones merge, just not cloud-dispatched.
# Requires cameras + orchestrator already up (`just up`). Stop: `just down`.
detect-local:
    #!/usr/bin/env bash
    set -uo pipefail
    curl -fsS -m 3 "http://localhost:{{port}}/zones" > /dev/null 2>&1 \
      || { echo "orchestrator not up on :{{port}} — run 'just up' first"; exit 1; }
    if ! lsof -nP -iTCP:{{go2rtc_rtsp_port}} -sTCP:LISTEN > /dev/null 2>&1; then
      echo "go2rtc RTSP not up on :{{go2rtc_rtsp_port}} — run 'just up' first"; exit 1
    fi
    if [[ ! -f "{{yolo_model}}" ]]; then
      echo "YOLO model not found: {{yolo_model}} (set YOLO_MODEL=...)"; exit 1
    fi
    mkdir -p {{state_dir}}
    pkill -f "edge-sensor --node-id" 2>/dev/null || true
    echo "→ sensor-north  (cam-outside RTSP → YOLO)…"
    uv run edge-sensor --node-id=sensor-north \
      --rtsp-url="rtsp://localhost:{{go2rtc_rtsp_port}}/cam-outside" \
      --orchestrator="http://localhost:{{port}}" \
      --yolo-model="{{yolo_model}}" \
      --db="{{state_dir}}/sensor-north.db" > {{sensor_north_log}} 2>&1 &
    echo $! > {{sensor_north_pid}}
    echo "→ sensor-south  (cam-inside RTSP → YOLO)…"
    uv run edge-sensor --node-id=sensor-south \
      --rtsp-url="rtsp://localhost:{{go2rtc_rtsp_port}}/cam-inside" \
      --orchestrator="http://localhost:{{port}}" \
      --yolo-model="{{yolo_model}}" \
      --db="{{state_dir}}/sensor-south.db" > {{sensor_south_log}} 2>&1 &
    echo $! > {{sensor_south_pid}}
    echo
    echo "  ✓ local detection started (no cloud). First-frame YOLO warmup ~10-30s."
    echo "    Watch: just logs   ·   dashboard: http://localhost:{{port}}   ·   stop: just down"

# record [SECONDS=15]: capture BOTH cameras to two independent, start-synced
# MP4 files, then open a synced two-up player in the browser. Records from the
# live go2rtc RTSP streams if the demo is up (no camera double-open); otherwise
# captures the cameras directly (same Anker / CAMERA_TEST / EDGE_CAM_* rules as
# `just up`). Files land in recordings/<timestamp>/. Ctrl-C stops the viewer.
record SECONDS='15':
    #!/usr/bin/env bash
    set -uo pipefail
    command -v ffmpeg > /dev/null || { echo "ffmpeg not on PATH"; exit 1; }
    secs="{{SECONDS}}"
    outdir="recordings/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$outdir"

    if lsof -nP -iTCP:{{go2rtc_rtsp_port}} -sTCP:LISTEN > /dev/null 2>&1; then
      echo "→ recording ${secs}s from the live go2rtc RTSP streams…"
      ffmpeg -hide_banner -loglevel error -rtsp_transport tcp \
        -i "rtsp://localhost:{{go2rtc_rtsp_port}}/cam-outside" -t "$secs" \
        -c copy -an -movflags +faststart "$outdir/cam-outside.mp4" & p1=$!
      ffmpeg -hide_banner -loglevel error -rtsp_transport tcp \
        -i "rtsp://localhost:{{go2rtc_rtsp_port}}/cam-inside" -t "$secs" \
        -c copy -an -movflags +faststart "$outdir/cam-inside.mp4" & p2=$!
    else
      # Direct capture — cameras must be free. Same precedence as
      # scripts/render-go2rtc-yaml.sh (keep in sync).
      VB=$(ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
        | awk '/AVFoundation video devices/{p=1;next}/AVFoundation audio devices/{p=0}p')
      if [[ -n "${EDGE_CAM_OUTSIDE_IDX:-}" && -n "${EDGE_CAM_INSIDE_IDX:-}" ]]; then
        O="$EDGE_CAM_OUTSIDE_IDX"; I="$EDGE_CAM_INSIDE_IDX"
      elif [[ "${CAMERA_TEST:-}" == "true" ]]; then
        O=$(printf '%s\n' "$VB" | grep -iE '\[[0-9]+\].*brio' | sed -E 's/.*\[([0-9]+)\].*/\1/' | head -1)
        I=$(printf '%s\n' "$VB" | grep -iE '\[[0-9]+\].*macbook' | grep -ivE 'desk view' | sed -E 's/.*\[([0-9]+)\].*/\1/' | head -1)
      else
        A=( $(printf '%s\n' "$VB" | grep -iE '\[[0-9]+\][[:space:]].*anker' | sed -E 's/.*\[([0-9]+)\].*/\1/' || true) )
        if [[ ${#A[@]} -lt 2 ]]; then
          echo "error: need 2 AnkerWork cameras (found ${#A[@]}); or CAMERA_TEST=true, or EDGE_CAM_*"; exit 1
        fi
        O="${A[0]}"; I="${A[1]}"
      fi
      if [[ -z "${O:-}" || -z "${I:-}" ]]; then echo "could not resolve two cameras"; exit 1; fi
      echo "→ recording ${secs}s directly from cameras $O + $I…"
      ffmpeg -hide_banner -loglevel error -f avfoundation -i "$O" \
        -t "$secs" -vf scale=-2:480 -r 15 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -an -movflags +faststart \
        "$outdir/cam-outside.mp4" & p1=$!
      ffmpeg -hide_banner -loglevel error -f avfoundation -i "$I" \
        -t "$secs" -vf scale=-2:480 -r 15 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -an -movflags +faststart \
        "$outdir/cam-inside.mp4" & p2=$!
    fi

    wait "$p1" "$p2" || true
    if [[ ! -s "$outdir/cam-outside.mp4" || ! -s "$outdir/cam-inside.mp4" ]]; then
      echo "✗ one or both recordings are empty (cameras busy / wrong source?). See above."; exit 1
    fi
    cp public/record-viewer.html "$outdir/index.html"
    echo "  ✓ wrote $outdir/{cam-outside,cam-inside}.mp4"

    port=8091
    ( cd "$outdir" && exec python3 -m http.server "$port" > /dev/null 2>&1 ) & srv=$!
    cleanup() { trap - INT TERM EXIT; kill "$srv" 2>/dev/null || true; echo; echo "  ✓ viewer stopped (files kept in $outdir)"; }
    trap cleanup INT TERM EXIT
    echo "  ▶ view both, synced:  http://localhost:$port/    (Ctrl-C to stop the viewer)"
    command -v open > /dev/null && open "http://localhost:$port/" || true
    wait "$srv"

# preview: clean LIVE two-up of both cameras in the browser (go2rtc players),
# so you can confirm the cameras are capturing properly in real time. Requires
# the streams to be up (`just up`). Ctrl-C stops the viewer.
preview:
    #!/usr/bin/env bash
    set -uo pipefail
    if ! curl -fsS -m 3 "http://localhost:{{go2rtc_port}}/api/streams" > /dev/null 2>&1; then
      echo "go2rtc not up on :{{go2rtc_port}} — run 'just up' first"; exit 1
    fi
    port=8092
    ( exec python3 -m http.server "$port" --directory public > /dev/null 2>&1 ) & srv=$!
    cleanup() { trap - INT TERM EXIT; kill "$srv" 2>/dev/null || true; echo; echo "  ✓ preview stopped"; }
    trap cleanup INT TERM EXIT
    url="http://localhost:$port/live-viewer.html"
    echo "  ▶ LIVE preview (both cameras): $url    (Ctrl-C to stop)"
    command -v open > /dev/null && open "$url" || true
    wait "$srv"

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

# ── tunnel (OPT-IN egress escape hatch — NOT the default path) ────────────
# `just up` talks to Expanso Cloud DIRECTLY. Only reach for this when a
# venue actually blocks non-443 outbound TCP, which stops expanso-edge from
# reaching NATS (4222), expanso-cli from reaching the API (9010), and OTLP
# telemetry (4318) from shipping. Those three streams then route through a
# jump box with clean egress.
#
# Verify you need it before turning it on — a plain TCP connect is enough:
#   timeout 8 bash -c '</dev/tcp/<network-id>.us2.cloud.expanso.io/4222' \
#     && echo "direct works, skip the tunnel"
#
# IMPORTANT: `tunnel-bootstrap` installs a PERMANENT DNS override while the
# tunnel itself is ephemeral. If the tunnel dies (jump box renamed, SSH alias
# gone, laptop moved networks) the override stays and points the cluster at a
# dead loopback port — everything cloud-facing hangs with no obvious cause.
# `just tunnel-unbootstrap` is the way back to the direct path. `just up`
# preflights for exactly this state via scripts/check-egress.sh.
#
# Architecture:
#   - sniproxy on hetzner-main reads the TLS SNI from each incoming
#     ClientHello and dials the matching *.us2.cloud.expanso.io cluster,
#     so any new cluster works without touching this end.
#   - local dnsmasq wildcards *.us2.cloud.expanso.io → 127.0.0.1 (via
#     /etc/resolver/us2.cloud.expanso.io). cloud.expanso.io itself (the
#     web UI) is unaffected and keeps using public DNS.
#   - SSH ControlMaster forwards 4222 + 9010 → sniproxy, 4318 → static
#     telemetry host.
# End-to-end TLS validates against the real cluster certs; no skip-verify.

# one-time DNS resolver + /etc/hosts setup (asks for sudo once).
# ONLY needed on a network that blocks outbound 4222/9010.
tunnel-bootstrap:
    @./scripts/tunnel-bootstrap.sh

# undo tunnel-bootstrap — removes the DNS override, restores direct egress.
# Run this if `just up` reports a leftover override.
tunnel-unbootstrap:
    @./scripts/tunnel-unbootstrap.sh

# start dnsmasq + autossh (no sudo). opt-in; `just up` no longer calls this.
tunnel-up:
    @./scripts/tunnel-up.sh

# stop dnsmasq + autossh. called by `just down` (no-op on the direct path).
tunnel-down:
    @./scripts/tunnel-down.sh

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
    # expanso-edge starts probing NATS within milliseconds of launch, so
    # confirm egress is sane first. Default path is DIRECT to the cloud —
    # no jump box. This mainly catches a leftover tunnel DNS override,
    # which would silently point the cluster at a dead loopback port.
    echo "→ checking egress to Expanso Cloud…"
    ./scripts/check-egress.sh
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
    nohup expanso-edge run --data-dir {{edge_data}} --api-listen {{edge_api_listen}} >> {{edge_log}} 2>&1 &
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
    # Capture BOTH Ankers from a SINGLE ffmpeg process (dual-cam-capture.sh)
    # and publish each to go2rtc via RTSP. One process = one device enumeration,
    # so the two feeds can NEVER be the same camera (the per-stream exec model
    # spawned two processes that, with identical-named C310s, could collide).
    echo "→ starting dual-camera capture (one process → both Ankers)…"
    ./scripts/dual-cam-capture.sh "rtsp://127.0.0.1:{{go2rtc_rtsp_port}}" > {{dualcam_log}} 2>&1 &
    echo $! > {{dualcam_pid}}
    # Wait until BOTH streams have landed a producer (the dual push connected).
    echo "→ waiting for both camera feeds to publish…"
    for _ in $(seq 1 20); do
      out=$(curl -fsS -o /dev/null -w '%{size_download}' --max-time 8 "http://localhost:{{go2rtc_port}}/api/frame.jpeg?src=cam-outside" 2>/dev/null || echo 0)
      in=$(curl -fsS -o /dev/null -w '%{size_download}' --max-time 8 "http://localhost:{{go2rtc_port}}/api/frame.jpeg?src=cam-inside" 2>/dev/null || echo 0)
      [[ "$out" -gt 0 && "$in" -gt 0 ]] && break
      sleep 1
    done
    echo
    echo "  ✓ expanso-edge  PID $(cat {{edge_pid}})        log: {{edge_log}}"
    echo "  ✓ orchestrator  PID $(cat {{orch_pid}})         log: {{orch_log}}"
    echo "  ✓ go2rtc        PID $(cat {{go2rtc_pid}})       log: {{go2rtc_log}}"
    echo
    echo "  dashboard:  http://localhost:{{port}}    (cameras streaming, NO detection yet)"
    echo "  cloud node: expanso-cli node list           (Mac registered as M5-Max in armyx-tech)"
    echo
    echo "  ── Next: turn on detection from Expanso Cloud ──"
    echo "    1. Open https://cloud.expanso.io and select the armyx-tech cluster"
    echo "    2. Navigate to Jobs → sensor-north → click Start"
    echo "    3. Same for sensor-south"
    echo "    Watch the dashboard light up with bracketed detections."
    echo "    Stop later: click Stop in the UI, or 'just detect-off'."
    echo
    echo "  First-time-on-this-cluster: 'just deploy-jobs' to push the YAML specs."
    echo "  CLI shortcut (no UI):       'just detect-on' starts both at once."
    echo
    echo "  follow: just logs    stop: just down"
    echo "  First run: macOS may prompt your terminal for Camera permission."

# Checks egress + bootstraps once if needed (same as `just up`)
# but doesn't touch the orchestrator/go2rtc — watch the node's cloud connection
# live. Ctrl-C stops it.
# start ONLY the Expanso Edge node in the foreground, streaming logs to stdout
edge:
    #!/usr/bin/env bash
    set -euo pipefail
    # Load .env so any EXPANSO_* / ARMYX_* vars are exported before the binary.
    if [[ -f .env ]]; then set -a; source .env; set +a; fi
    # The node reaches cloud.expanso.io (NATS) directly; this just catches a
    # leftover tunnel DNS override that would send it to a dead loopback port.
    ./scripts/check-egress.sh
    # Bootstrap once if we've never registered this data-dir.
    if [[ ! -f {{edge_creds}} ]]; then
      echo "→ bootstrapping expanso-edge to cloud.expanso.io (one-time)…"
      mkdir -p {{edge_data}}
      expanso-edge bootstrap --data-dir {{edge_data}}
    fi
    # Refresh node labels (idempotent) so per-platform job selectors target us.
    mkdir -p {{edge_data}}/config.d
    cp scripts/expanso-edge-labels.yaml {{edge_data}}/config.d/20-labels.yaml
    echo "→ expanso-edge running in foreground; logs below. Ctrl-C to stop."
    echo "  (local node API on {{edge_api_listen}} — 9011, NOT 9010, to stay clear of the opt-in tunnel)"
    # exec → Ctrl-C goes straight to the daemon; logs stream to this terminal.
    exec expanso-edge run --data-dir {{edge_data}} --api-listen {{edge_api_listen}}

# stop orchestrator + sensors + go2rtc; verify ports free
down:
    #!/usr/bin/env bash
    set -euo pipefail
    for f in {{sensor_north_pid}} {{sensor_south_pid}} {{orch_pid}} {{go2rtc_pid}} {{dualcam_pid}} {{edge_pid}}; do
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
    pkill -f "dual-cam-capture" 2>/dev/null || true   # the capture supervisor loop
    pkill -f "avfoundation" 2>/dev/null || true       # its ffmpeg child holding the cameras
    pkill -f "{{go2rtc_bin}}" 2>/dev/null || true
    pkill -f "expanso-edge run" 2>/dev/null || true
    # Tunnel goes down LAST — orderly teardown so in-flight NATS sessions
    # see EOF rather than getting cut mid-message. No-op on the default
    # direct path; only does work if you opted into `just tunnel-up`.
    ./scripts/tunnel-down.sh
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
    # Look up each role by a process-cmdline grep instead of trusting
    # .demo-state/*.pid files. Sensors are cluster-dispatched now (the
    # expanso-edge daemon spawns them as job executions), so their PIDs
    # never land in .demo-state/. The old "pidfile-exists" check would
    # incorrectly report sensor-north / sensor-south as "not running"
    # even when they were emitting events.
    declare -a roles=(
      "orchestrator|edge-orchestrator --port"
      "sensor-north|edge-sensor --node-id=sensor-north"
      "sensor-south|edge-sensor --node-id=sensor-south"
      "go2rtc|bin/go2rtc -c"
      "expanso-edge|expanso-edge run --data-dir"
    )
    for r in "${roles[@]}"; do
      label="${r%%|*}"
      pat="${r#*|}"
      # Use the first matching PID — there may be multiple (e.g. `uv run`
      # parent + the actual python child).
      pid=$(pgrep -f "$pat" | head -1)
      if [[ -n "$pid" ]]; then
        echo "  $label: running (PID $pid)"
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

# Push sensor-north / sensor-south YAML specs to the cluster and leave
# them in `stopped` state. After this, the jobs exist in the control
# plane and show up at https://cloud.expanso.io — the operator can click
# "Start" in the UI to dispatch them to this Mac (selector
# site=laptop-demo). One-time setup per cluster; safe to re-run (the
# deploy is a versioned update; stop is idempotent with --force).
deploy-jobs:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v expanso-cli > /dev/null \
      || { echo "expanso-cli not on PATH"; exit 1; }
    for f in jobs/sensor-north-job.yaml jobs/sensor-south-job.yaml jobs/fuse-job.yaml; do
      echo "→ pushing $f to cluster"
      expanso-cli job deploy --force "$f"
    done
    sleep 1
    for n in sensor-north sensor-south fuse; do
      echo "→ stopping $n so cloud UI can start it on demand"
      expanso-cli job stop --force "$n" 2>/dev/null || true
    done
    echo
    echo "  ✓ jobs are in the cluster, stopped. Start them via:"
    echo "      cloud UI:  https://cloud.expanso.io   (pick armyx-tech cluster → Jobs)"
    echo "      OR:        just detect-on   (sensors only)"
    echo "                 just fuse-on     (adds the cross-sector fusion pipeline)"

# Start sensor-north + sensor-south on the cluster from the CLI. Same
# effect as clicking Start in the cloud UI on each job. Useful for
# scripted demos and CI; the headline demo uses the UI button.
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

# Start the cross-sector fusion pipeline (jobs/fuse-job.yaml). The
# audience-facing version of "now we turn on the fusion stage" is to
# click Start on `fuse` in cloud.expanso.io. This is the CLI shortcut.
fuse-on:
    @command -v expanso-cli > /dev/null || { echo "expanso-cli not on PATH"; exit 1; }
    @echo "→ deploying jobs/fuse-job.yaml"
    @expanso-cli job deploy --force jobs/fuse-job.yaml
    @echo "  ✓ fuse pipeline running. Watch via:"
    @echo "    expanso-cli job logs fuse    (raw Bloblang output)"
    @echo "    tail -f .demo-state/fused-alerts.ndjson    (signed fused alerts)"

# Stop the fusion pipeline. Sensors continue emitting; orchestrator's
# in-process correlator.py keeps the dashboard's alert strip alive
# (since fuse hasn't fully replaced it yet — that's a separate refactor).
fuse-off:
    @command -v expanso-cli > /dev/null || { echo "expanso-cli not on PATH"; exit 1; }
    @echo "→ stopping fuse"
    @expanso-cli job stop --force fuse 2>/dev/null || echo "  (was not running)"

# ── Jetson Expanso deploy ──────────────────────────────────────────────────

# deploy + start all Expanso jobs (fusion-node, sensor-north, sensor-south, fuse)
deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v expanso-cli > /dev/null || { echo "expanso-cli not on PATH"; exit 1; }
    # `expanso-cli job deploy` auto-rolls-out (starts) the job — there is no
    # `job start` verb (that just printed the help dump). Deploy is enough.
    for f in {{job_files}}; do
      echo "→ deploying $f (auto-starts via rollout)"
      expanso-cli job deploy "$f"
    done
    echo "  ✓ deployed + rolled out. status: just deploy-status"

# stop + remove all Expanso jobs (fusion-node, sensor-north, sensor-south, fuse)
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

# show cluster state for the jobs (+ event-archive)
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
