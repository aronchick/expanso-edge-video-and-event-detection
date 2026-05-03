#!/usr/bin/env bash
# Pre-demo checklist — run 30 minutes before going on stage.
# Per HACKATHON_SCRIPT.md §15.
#
# Each check prints OK or FAIL. Don't go on stage with any FAIL except where
# explicitly tagged optional.
#
# Required env:
#   REOLINK_USER, REOLINK_PASS_NORTH, REOLINK_PASS_SOUTH
#   GOOGLE_API_KEY (for Gemini reachability check)

set -u

PASS=0
FAIL=0

ok()   { echo "OK   $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL $1"; FAIL=$((FAIL+1)); }

check() {
  local label="$1"; shift
  if "$@" > /dev/null 2>&1; then ok "$label"; else fail "$label"; fi
}

echo "=== Edge-ISR pre-demo check ==="
echo

# 1. Network
echo "-- Network --"
check "Reolink north (.50.11) reachable"  ping -c 1 -W 2 192.168.50.11
check "Reolink south (.50.12) reachable"  ping -c 1 -W 2 192.168.50.12
check "Jetson (.50.20) reachable"         ping -c 1 -W 2 192.168.50.20
check "Laptop fusion node (.50.30) reachable" ping -c 1 -W 2 192.168.50.30
echo

# 2. Internet (for Gemini)
echo "-- Cloud reachback --"
if curl -s --max-time 5 https://generativelanguage.googleapis.com > /dev/null; then
  ok "generativelanguage.googleapis.com TCP reachable"
else
  fail "generativelanguage.googleapis.com unreachable (DDIL demo will work, Gemini won't)"
fi
echo

# 3. RTSP streams
echo "-- RTSP --"
if [[ -n "${REOLINK_USER:-}" && -n "${REOLINK_PASS_NORTH:-}" ]]; then
  if ffprobe -v error -rtsp_transport tcp \
        -i "rtsp://${REOLINK_USER}:${REOLINK_PASS_NORTH}@192.168.50.11:554/h264Preview_01_sub" \
        -show_streams 2>&1 | grep -q codec_name; then
    ok "RTSP north substream"
  else
    fail "RTSP north substream"
  fi
else
  fail "REOLINK_USER / REOLINK_PASS_NORTH not set, skipping RTSP north"
fi
if [[ -n "${REOLINK_USER:-}" && -n "${REOLINK_PASS_SOUTH:-}" ]]; then
  if ffprobe -v error -rtsp_transport tcp \
        -i "rtsp://${REOLINK_USER}:${REOLINK_PASS_SOUTH}@192.168.50.12:554/h264Preview_01_sub" \
        -show_streams 2>&1 | grep -q codec_name; then
    ok "RTSP south substream"
  else
    fail "RTSP south substream"
  fi
else
  fail "REOLINK_USER / REOLINK_PASS_SOUTH not set, skipping RTSP south"
fi
echo

# 4. Expanso jobs (all four)
echo "-- Expanso jobs --"
if command -v expanso-cli > /dev/null; then
  # fusion-node should ALWAYS be running pre-demo (it serves the dashboard).
  # The 3 workload jobs are stopped at Beat 0 lights-up; precheck flags
  # them deployed-but-not-running rather than failing.
  for job in fusion-node sensor-north sensor-south armyx-tech-event-archive; do
    if expanso-cli job list 2>/dev/null | grep -q "${job}.*Running"; then
      ok "${job} running"
    elif [[ "$job" == "fusion-node" ]]; then
      fail "${job} not running (dashboard will be blank!)"
    elif expanso-cli job describe "${job}" >/dev/null 2>&1; then
      ok "${job} deployed but stopped (correct for Beat 0 lights-up)"
    else
      fail "${job} not deployed — run scripts/demo_deploy_all.sh first"
    fi
  done
else
  fail "expanso-cli not on PATH (fusion-node may still work via direct uv run)"
fi
echo

# 5. Orchestrator endpoints
echo "-- Orchestrator endpoints --"
ORCH="http://192.168.50.30:8080"
check "GET /triggers"          curl -fsS --max-time 2 "$ORCH/triggers"
check "GET /metrics"           curl -fsS --max-time 2 "$ORCH/metrics"
check "GET /jobs"              curl -fsS --max-time 2 "$ORCH/jobs"
check "GET /snapshot/sensor-north" curl -fsS --max-time 2 -o /dev/null "$ORCH/snapshot/sensor-north"
check "GET /snapshot/sensor-south" curl -fsS --max-time 2 -o /dev/null "$ORCH/snapshot/sensor-south"
check "GET / (dashboard)"      curl -fsS --max-time 2 -o /dev/null "$ORCH/"
echo

# 6. Operator demo controls round-trip (so F1-F4 work mid-demo)
echo "-- Operator controls --"
if curl -fsS --max-time 2 -X POST "$ORCH/demo/wan-down" > /dev/null \
   && curl -fsS --max-time 2 -X POST "$ORCH/demo/wan-up" > /dev/null; then
  ok "WAN toggle round-trip (F1/F2)"
else
  fail "WAN toggle round-trip"
fi
if curl -fsS --max-time 2 -X POST "$ORCH/demo/fused-test" > /dev/null; then
  ok "synthetic fused alert (F3)"
else
  fail "synthetic fused alert"
fi
echo

# 7. Trigger pre-stage state — no aerial classes (Beat 3 needs them absent at T=0)
echo "-- Pre-stage trigger state --"
TRIGGERS=$(curl -fsS --max-time 2 "$ORCH/triggers" 2>/dev/null || echo "")
if echo "$TRIGGERS" | grep -q '"drone"'; then
  fail "trigger list already contains 'drone' — run the reset curl in STAGE_RUNBOOK.md before stage"
elif echo "$TRIGGERS" | grep -q '"airplane"'; then
  fail "trigger list already contains 'airplane' — run the reset curl in STAGE_RUNBOOK.md before stage"
else
  ok "trigger list lacks aerial classes (Beat 3 ready)"
fi
echo

# 8. End-to-end smoke
echo "-- End-to-end (camera walk) --"
echo "Walk in front of NORTH camera now..."
echo "(waiting 10s)"
sleep 10
METRICS=$(curl -fsS --max-time 2 "$ORCH/metrics" 2>/dev/null || echo "")
if echo "$METRICS" | grep -q '"events_per_minute"'; then
  EPM=$(echo "$METRICS" | sed -n 's/.*"events_per_minute":[[:space:]]*\([0-9.]*\).*/\1/p')
  if [[ -n "$EPM" && "$EPM" != "0" && "$EPM" != "0.0" ]]; then
    ok "recent events flowing (events/min=${EPM})"
  else
    fail "events/min is 0 — camera angle or trigger zone problem"
  fi
else
  fail "/metrics returned no events_per_minute field"
fi
echo

echo "=== Result: $PASS pass, $FAIL fail ==="
if [[ $FAIL -eq 0 ]]; then
  echo "Ready."
  exit 0
else
  echo "Fix failures before going on stage."
  exit 1
fi
