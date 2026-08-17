#!/usr/bin/env bash
# dual-cam-capture.sh [go2rtc-rtsp-base]
#
# Capture BOTH AnkerWork C310 webcams from a SINGLE ffmpeg process and publish
# each to a go2rtc RTSP mountpoint (cam-outside, cam-inside).
#
# WHY ONE PROCESS — this is the whole point:
#   The two C310s are IDENTICAL by name, and macOS assigns AVFoundation device
#   indices PER PROCESS, nondeterministically. Two SEPARATE ffmpeg processes
#   (the old per-stream go2rtc exec model) could each pick "an Anker" and
#   COLLIDE on the same physical camera — the dashboard then showed the same
#   feed twice. Opening BOTH cameras inside ONE ffmpeg process eliminates this:
#   within a single enumeration each physical device maps to exactly one index,
#   so -i <a> and -i <b> are ALWAYS two different cameras. Duplication is
#   structurally impossible, regardless of how macOS orders the devices.
#
# go2rtc receives the two streams as RTSP publishes (cam-outside / cam-inside
# are defined as passive ingest streams in go2rtc.yaml). Runs a restart loop so
# a transient camera glitch self-heals.
#
# Index selection:
#   * Default: the two AnkerWork cameras, by name (order is arbitrary — the
#     units are identical, so which is cam-outside vs cam-inside doesn't matter).
#   * EDGE_CAM_OUTSIDE_IDX + EDGE_CAM_INSIDE_IDX: force two literal indices
#     (for CAMERA_TEST / non-Anker setups).
set -uo pipefail

RTSP_BASE="${1:-rtsp://127.0.0.1:8554}"

# 640x480@30 is supported by both C310 units (same model). We avoid the 15fps
# mode — avfoundation advertises it but ffmpeg fails to open at 15.
CAP_SIZE="${EDGE_CAP_SIZE:-640x480}"
CAP_FPS="${EDGE_CAP_FPS:-30}"
# Pin the INPUT capture pixel format to one the C310 actually offers (nv12 /
# uyvy422 / yuyv422). Without this, ffmpeg leaks the OUTPUT '-pix_fmt yuv420p'
# onto the avfoundation input and dies with "Selected pixel format (yuv420p) is
# not supported by the input device." libx264 still encodes to yuv420p downstream.
CAP_PIXFMT="${EDGE_CAP_PIXFMT:-nv12}"

# First AVFoundation index whose device name matches $1 (case-insensitive),
# optionally excluding names matching $2.
_idx_by_name() {
  local include="$1" exclude="${2:-}"
  ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
    | awk '/AVFoundation video devices/{p=1; next} /AVFoundation audio devices/{p=0} p' \
    | grep -iE "\[[0-9]+\][[:space:]].*${include}" \
    | { [[ -n "$exclude" ]] && grep -viE "$exclude" || cat; } \
    | sed -E 's/.*\[([0-9]+)\].*/\1/' | head -1
}

resolve_ankers() {
  # Print the two AVFoundation indices to capture, space-separated, in this
  # process's own enumeration (the only ordering that matters for ffmpeg).
  if [[ -n "${EDGE_CAM_OUTSIDE_IDX:-}" && -n "${EDGE_CAM_INSIDE_IDX:-}" ]]; then
    echo "$EDGE_CAM_OUTSIDE_IDX $EDGE_CAM_INSIDE_IDX"
    return 0
  fi
  if [[ "${CAMERA_TEST:-}" == "true" ]]; then
    local bri mb
    bri="$(_idx_by_name 'brio' || true)"
    mb="$(_idx_by_name 'macbook' 'desk view' || true)"
    [[ -n "$bri" && -n "$mb" ]] || return 1
    echo "$bri $mb"
    return 0
  fi
  local idxs
  idxs=$(ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
    | awk '/AVFoundation video devices/{p=1; next} /AVFoundation audio devices/{p=0} p' \
    | grep -iE '\[[0-9]+\][[:space:]].*anker' \
    | sed -E 's/.*\[([0-9]+)\].*/\1/' \
    | sort -n)
  # shellcheck disable=SC2206
  local arr=( $idxs )
  if [[ ${#arr[@]} -lt 2 ]]; then
    return 1
  fi
  echo "${arr[0]} ${arr[1]}"
}

enc_opts=(
  -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline
  -pix_fmt yuv420p -b:v 600k -maxrate 600k -bufsize 1200k -g 30 -bf 0
)

while true; do
  if ! read -r A B < <(resolve_ankers); then
    echo "dual-cam: fewer than 2 AnkerWork cameras present — retrying in 3s" >&2
    sleep 3
    continue
  fi
  echo "dual-cam: capturing indices $A -> cam-outside, $B -> cam-inside" >&2

  # ONE process, two inputs, two RTSP outputs. -i A and -i B are guaranteed
  # distinct physical devices (single enumeration). Per-output downscale to a
  # light 480p@15 stream.
  ffmpeg -hide_banner -loglevel error \
    -f avfoundation -pixel_format "$CAP_PIXFMT" -framerate "$CAP_FPS" -video_size "$CAP_SIZE" -i "$A" \
    -f avfoundation -pixel_format "$CAP_PIXFMT" -framerate "$CAP_FPS" -video_size "$CAP_SIZE" -i "$B" \
    -map 0:v -vf scale=-2:480 -r 15 "${enc_opts[@]}" \
      -f rtsp -rtsp_transport tcp "$RTSP_BASE/cam-outside" \
    -map 1:v -vf scale=-2:480 -r 15 "${enc_opts[@]}" \
      -f rtsp -rtsp_transport tcp "$RTSP_BASE/cam-inside" \
    || true

  echo "dual-cam: ffmpeg exited; restarting in 2s" >&2
  sleep 2
done
