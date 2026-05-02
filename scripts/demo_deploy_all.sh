#!/usr/bin/env bash
# One-time deploy of the four demo jobs to the armyx-tech Expanso
# cluster. After deploy succeeds, jobs are STOPPED so the post-deploy
# state matches DEMO_SCRIPT.md Beat 0 lights-up: jobs deployed but not
# running, gray dots on the dashboard, ready for the operator to start
# them from the Expanso Cloud UI on stage.
#
# Run this once per venue / once per cluster reset. Between rehearsals,
# use ./scripts/demo_reset.sh (which stops jobs without re-deploying).
#
# Usage:
#   ./scripts/demo_deploy_all.sh                # deploy + verify Running + stop
#   ./scripts/demo_deploy_all.sh --leave-running  # deploy + verify, skip stop
#   ./scripts/demo_deploy_all.sh --dry          # just print what it would do
#
# Why deploy + stop instead of just deploy? Expanso's job deploy
# auto-starts the job on schedule. To leave it in the Beat 0
# lights-up state (deployed-but-stopped), we must explicitly stop
# each job after confirming it deployed and ran cleanly. The
# verification step (wait for Running) proves the job spec is valid
# before we put it to sleep.
#
# Per-job 10s timeout on the Running check; if any job fails to flip
# Running by then, exit non-zero so the operator knows there's a real
# spec problem before stage time.

set -uo pipefail

JOBS=(
  "jobs/orchestrator-job.yaml:orchestrator"
  "jobs/sensor-north-job.yaml:sensor-north"
  "jobs/sensor-south-job.yaml:sensor-south"
  "jobs/armyx-tech-event-archive.yaml:armyx-tech-event-archive"
)
PER_JOB_TIMEOUT_SEC=10
DRY=0
LEAVE_RUNNING=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry|--dry-run) DRY=1 ;;
    --leave-running) LEAVE_RUNNING=1 ;;
    --help|-h) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

command -v expanso-cli >/dev/null 2>&1 || {
  echo "FAIL  expanso-cli not on PATH"
  exit 1
}

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== armyx-tech demo deploy ==="
echo "  repo root  : $REPO_ROOT"
echo "  per-job timeout: ${PER_JOB_TIMEOUT_SEC}s"
echo

OVERALL_RC=0
for entry in "${JOBS[@]}"; do
  yaml="${entry%%:*}"
  name="${entry##*:}"

  if [[ ! -f "$yaml" ]]; then
    echo "FAIL  $name : $yaml missing"
    OVERALL_RC=1
    continue
  fi

  if [[ "$DRY" -eq 1 ]]; then
    echo "DRY   would deploy $name from $yaml"
    continue
  fi

  printf "INFO  deploying %-32s ... " "$name"
  if ! expanso-cli job deploy "$yaml" >/dev/null 2>&1; then
    echo "FAIL (expanso-cli job deploy non-zero)"
    OVERALL_RC=1
    continue
  fi
  echo "submitted"

  printf "INFO  waiting for %-32s Running ... " "$name"
  start=$(date +%s)
  status="unknown"
  while :; do
    out=$(expanso-cli job describe "$name" 2>/dev/null || true)
    status=$(echo "$out" | awk '/^State[[:space:]]*=/ {print $3; exit}')
    if [[ "$status" == "running" || "$status" == "Running" ]]; then
      echo "running ($(($(date +%s) - start))s)"
      break
    fi
    if [[ $(($(date +%s) - start)) -ge $PER_JOB_TIMEOUT_SEC ]]; then
      echo "TIMEOUT (state=${status:-unknown})"
      OVERALL_RC=1
      break
    fi
    sleep 1
  done
done

echo
if [[ "$OVERALL_RC" -ne 0 ]]; then
  echo "FAIL  one or more jobs did not reach Running within ${PER_JOB_TIMEOUT_SEC}s"
  echo "      The job spec is broken. Don't proceed to demo until fixed."
  echo "      ssh jetson 'sudo journalctl -u expanso-edge --since \"2 minutes ago\" -n 50' may help."
  exit "$OVERALL_RC"
fi

echo "OK    all jobs validated as Running"

if [[ "$LEAVE_RUNNING" -eq 1 ]]; then
  echo "INFO  --leave-running set; not stopping jobs."
  echo "      To return to Beat 0 lights-up state: ./scripts/demo_reset.sh --jobs"
  exit 0
fi

if [[ "$DRY" -eq 1 ]]; then
  echo "DRY   would stop each job to leave Beat 0 lights-up state"
  exit 0
fi

echo
echo "INFO  stopping jobs so post-deploy state matches Beat 0 lights-up"
for entry in "${JOBS[@]}"; do
  name="${entry##*:}"
  printf "INFO  stopping %-32s ... " "$name"
  if echo "y" | timeout 8 expanso-cli job stop "$name" >/dev/null 2>&1; then
    echo "stopped"
  else
    echo "WARN: stop failed (may already be stopped)"
  fi
done

echo
echo "OK    armyx-tech demo ready for Beat 0"
echo "      4 jobs deployed and verified, all currently stopped."
echo "      On stage, start them from the Expanso Cloud UI."
echo "      To re-rehearse: ./scripts/demo_reset.sh"
