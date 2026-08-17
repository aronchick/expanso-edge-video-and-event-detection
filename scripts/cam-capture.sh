#!/usr/bin/env bash
# cam-capture.sh <camera-selector> <go2rtc-output-url>
#
# <camera-selector> is one of:
#   * "anker<N>@<fallback>"  — the Nth AnkerWork C310 (0-based), resolved BY NAME
#     at the instant of capture, in THIS process. Drift-proof: macOS reorders
#     AVFoundation indices between processes, so we re-resolve here rather than
#     trust a number baked at render time. If enumeration finds no cameras
#     (e.g. a TCC-permission edge case in the exec child), fall back to the
#     literal index <fallback>, which render-go2rtc-yaml.sh resolved seconds ago.
#   * "anker<N>"             — same, but hard-fail if enumeration finds < N+1.
#   * a bare integer         — a literal AVFoundation index (CAMERA_TEST /
#     EDGE_CAM_* override paths that select non-Anker cameras).
#
# Open the chosen webcam at one of its ACTUALLY-supported modes and stream it to
# go2rtc's {output} as RTSP. go2rtc execs this once per camera, on demand.
#
# Why a wrapper instead of a fixed ffmpeg line:
#   * ffmpeg/avfoundation defaults to 29.97fps (NTSC), which these webcams
#     reject ("Selected framerate 29.970030 is not supported").
#   * Identical AnkerWork C310 units expose DIFFERENT mode sets, so the mode
#     must be probed for THIS device immediately before opening it.
#
# Picks the smallest resolution (low throughput) at the device's MAX advertised
# framerate, then downscales/caps on output to a light 480p@15 stream. Falls
# back to 640x480@30.
#
# IMPORTANT — pick the MAX framerate, not the min: avfoundation advertises each
# mode as a [min max] range (e.g. 640x480@[15 30]), but ffmpeg can FAIL to open
# at the min ("Selected framerate (15.0) is not supported by the device") even
# though it's listed. 30 opens reliably. One of our two C310s only exposes
# 640x480@[15 30] (no 360p mode), so taking the min silently picked 15 and that
# camera never streamed — the classic "only one feed" symptom.
set -uo pipefail

SEL="${1:?usage: cam-capture.sh <anker0@idx|anker0|index> <output-url>}"
OUT="${2:?usage: cam-capture.sh <anker0@idx|anker0|index> <output-url>}"

DEBUG_LOG="${CAM_CAPTURE_DEBUG_LOG:-/tmp/cam-capture-debug.log}"
log() { printf '%s sel=%s %s\n' "$(date '+%H:%M:%S')" "$SEL" "$1" >> "$DEBUG_LOG" 2>/dev/null || true; }

# Print the AVFoundation indices of all AnkerWork cameras THIS process can see,
# ascending. Empty if none (or if the child can't enumerate cameras).
list_anker_indices() {
  ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
    | awk '/AVFoundation video devices/{p=1; next} /AVFoundation audio devices/{p=0} p' \
    | grep -iE '\[[0-9]+\][[:space:]].*anker' \
    | sed -E 's/.*\[([0-9]+)\].*/\1/' \
    | sort -n
}

resolve_idx() {
  local sel="$1"
  # bare integer → literal index
  if [[ "$sel" =~ ^[0-9]+$ ]]; then
    echo "$sel"; return 0
  fi
  # anker<N> or anker<N>@<fallback>
  if [[ "$sel" =~ ^anker([0-9]+)(@([0-9]+))?$ ]]; then
    local ord="${BASH_REMATCH[1]}" fb="${BASH_REMATCH[3]:-}"
    local idxs; idxs=$(list_anker_indices)
    local count; count=$(printf '%s\n' "$idxs" | grep -c '[0-9]' || true)
    log "enumerated anker indices=[$(echo $idxs | tr '\n' ' ')] count=$count ord=$ord fallback=${fb:-none}"
    if [[ "$count" -ge $((ord + 1)) ]]; then
      printf '%s\n' "$idxs" | awk -v n="$ord" 'NR==n+1{print; exit}'
      return 0
    fi
    if [[ -n "$fb" ]]; then
      log "enumeration insufficient; using render-time fallback index $fb"
      echo "$fb"; return 0
    fi
    return 1
  fi
  return 1
}

if ! IDX="$(resolve_idx "$SEL")"; then
  log "FAILED to resolve a device index"
  echo "cam-capture: could not resolve a camera for selector '$SEL'" >&2
  exit 1
fi
log "opening avfoundation index $IDX"

# Force an impossible size so ffmpeg prints the device's "Supported modes:".
probe="$(ffmpeg -hide_banner -f avfoundation -video_size 1x1 -i "$IDX" 2>&1 || true)"
read -r SIZE FPS <<EOF
$(printf '%s\n' "$probe" \
  | grep -oE '[0-9]+x[0-9]+@\[[0-9.]+ [0-9.]+\]fps' \
  | sed -E 's/([0-9]+)x([0-9]+)@\[[0-9.]+ ([0-9.]+)\]fps/\1 \2 \3/' \
  | awk '{w=$1; h=$2; f=int($3+0.5); a=w*h;
          r=5; if(f==30)r=0; else if(f==24)r=1; else if(f==20)r=2; else if(f==15)r=3; else if(f==60)r=4;
          print a*10+r, w"x"h, f}' \
  | sort -n | head -1 | awk '{print $2, $3}')
EOF

if [[ -z "${SIZE:-}" || -z "${FPS:-}" ]]; then
  SIZE="640x480"; FPS="30"
fi
log "mode ${SIZE}@${FPS} on index $IDX"

exec ffmpeg -hide_banner -loglevel error \
  -f avfoundation -framerate "$FPS" -video_size "$SIZE" -i "$IDX" \
  -vf scale=-2:480 -r 15 \
  -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline -pix_fmt yuv420p \
  -b:v 600k -maxrate 600k -bufsize 1200k -g 30 -bf 0 \
  -f rtsp -rtsp_transport tcp "$OUT"
