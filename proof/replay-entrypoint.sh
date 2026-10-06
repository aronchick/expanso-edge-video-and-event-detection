#!/usr/bin/env bash
# Start a local Expanso Edge agent and deploy /work/job.yaml, whose input and
# output scripts/edge-replay.py already pointed at files under /work. A file
# input reads once and the job then completes; wait for that, or for a failure
# or the timeout, and stop.
set -euo pipefail

TIMEOUT="${REPLAY_TIMEOUT:-60}"

mkdir -p /tmp/edge
expanso-edge run --local --no-watch --data-dir /tmp/edge \
  --log-format text --log-level warn >/work/edge.log 2>&1 &
EDGE_PID=$!
trap 'kill "$EDGE_PID" 2>/dev/null || true; wait "$EDGE_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 60); do
  expanso-cli health >/dev/null 2>&1 && break
  sleep 0.25
done

expanso-cli job deploy /work/job.yaml --force >/work/deploy.log 2>&1 \
  || { cat /work/deploy.log >&2; exit 3; }

deadline=$((SECONDS + TIMEOUT))
while [ "$SECONDS" -lt "$deadline" ]; do
  state="$(expanso-cli job list 2>/dev/null | awk -F'│' 'NR>1 {gsub(/ /, "", $5); print $5}' | head -1)"
  case "$state" in
    completed) sleep 0.5; exit 0 ;;
    failed) cat /work/edge.log >&2; echo "job failed" >&2; exit 5 ;;
  esac
  sleep 0.25
done

cat /work/edge.log >&2
echo "timed out after ${TIMEOUT}s" >&2
exit 4
