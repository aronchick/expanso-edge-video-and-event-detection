#!/usr/bin/env bash
# Reset the armyx-tech demo state to "Beat 0 lights-up" between
# rehearsals: stop the four cluster jobs (without deleting them — the
# specs stay in the cluster, just paused) and clear local state files.
# Idempotent — safe to run when jobs are already stopped or absent.
#
# After this script runs, you are in DEMO_SCRIPT.md Beat 0 lights-up
# state: dashboard shows 0/4 running, four gray "stopped" dots, ready
# for the operator to start them from the Expanso Cloud UI on stage.
#
# Usage:
#   ./scripts/demo_reset.sh           # stop jobs + clear local state
#   ./scripts/demo_reset.sh --jobs    # stop jobs only
#   ./scripts/demo_reset.sh --local   # clear local state only
#
# What gets touched (default mode):
#   - expanso-cli job stop  : sensor-north, sensor-south,
#                             armyx-tech-event-archive
#                             (job specs stay in cluster; can be
#                              restarted via UI or `expanso-cli job rerun`)
#                             NOT touched: fusion-node (it serves the
#                             dashboard — stopping it would blank the
#                             conference monitor between rehearsals).
#   - rm -f                 : orchestrator.db, events.ndjson,
#                             triggers.yaml  (in repo root)
#
# To completely UN-deploy (remove specs from cluster), use
# `expanso-cli job delete <name>` for each. Not what this script does.

set -uo pipefail

# Only the 3 workload jobs. The fusion-node (local FastAPI) stays
# running so the dashboard keeps serving between rehearsals — stopping
# it would blank the conference monitor.
JOBS=(
  armyx-tech-event-archive
  sensor-south
  sensor-north
)
LOCAL_FILES=(
  orchestrator.db
  events.ndjson
  triggers.yaml
)

DO_JOBS=1
DO_LOCAL=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --jobs)  DO_LOCAL=0 ;;
    --local) DO_JOBS=0 ;;
    --help|-h) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== armyx-tech demo reset ==="

if [[ "$DO_JOBS" -eq 1 ]]; then
  if ! command -v expanso-cli >/dev/null 2>&1; then
    echo "WARN  expanso-cli not on PATH; skipping job stops"
  else
    for name in "${JOBS[@]}"; do
      printf "INFO  stopping %-32s ... " "$name"
      # `expanso-cli job stop` prompts for confirmation; pipe "y\n".
      if echo "y" | timeout 8 expanso-cli job stop "$name" >/dev/null 2>&1; then
        echo "stopped"
      else
        echo "not running (or stop failed; safe to ignore)"
      fi
    done
  fi
fi

if [[ "$DO_LOCAL" -eq 1 ]]; then
  for f in "${LOCAL_FILES[@]}"; do
    if [[ -e "$f" ]]; then
      rm -f "$f"
      echo "INFO  removed $f"
    fi
  done
fi

echo
echo "OK    demo reset complete"
echo "      Run ./scripts/demo_deploy_all.sh to redeploy for Beat 0."
