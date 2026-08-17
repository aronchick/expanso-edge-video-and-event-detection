#!/usr/bin/env bash
# Render go2rtc.yaml from go2rtc.yaml.template by resolving two webcam
# AVFoundation indices RIGHT NOW. macOS reorders webcam indices between
# processes, so static index pins drift; resolving at startup keeps the
# vulnerability window down to the few seconds before go2rtc spawns its
# ffmpeg children.
#
# Runs on the AnkerWork cameras ONLY by default. If fewer than two Ankers
# are present it warns and exits non-zero (never silently grabs the Brio,
# built-in, or a virtual camera). Override with EDGE_CAM_OUTSIDE_IDX /
# EDGE_CAM_INSIDE_IDX to force specific indices for any cameras.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$ROOT/go2rtc.yaml.template"
OUT="$ROOT/go2rtc.yaml"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

if [[ ! -f "$TEMPLATE" ]]; then
  echo "error: $TEMPLATE missing" >&2
  exit 1
fi

# Enumerate AVFoundation video devices. ffmpeg prints to stderr; exits non-zero
# because no -i was given. We swallow the exit and grep its stderr.
LIST=$(ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 || true)

# Pull lines that look like:  [AVFoundation indev @ 0x...] [N] DEVICE NAME
# and stop at the audio devices section (which restarts numbering).
VIDEO_BLOCK=$(printf '%s\n' "$LIST" \
  | awk '/AVFoundation video devices/{p=1; next} /AVFoundation audio devices/{p=0} p')

# Resolve the two camera indices. STRICT by design:
#   1. Explicit override:  EDGE_CAM_OUTSIDE_IDX / EDGE_CAM_INSIDE_IDX (any cams)
#   2. Otherwise: AnkerWork cameras ONLY. Fewer than two Ankers → WARN AND QUIT.
# We never silently fall back to the Brio / built-in / virtual cameras — a
# booth demo streaming the wrong camera is worse than failing loudly.
# Helper: first AVFoundation index whose name matches $1 (case-insensitive),
# optionally excluding names matching $2. Matched by name so it survives the
# index reordering macOS does between processes.
_idx_by_name() {
  local include="$1" exclude="${2:-}"
  printf '%s\n' "$VIDEO_BLOCK" \
    | grep -iE "\[[0-9]+\][[:space:]].*${include}" \
    | { [[ -n "$exclude" ]] && grep -viE "$exclude" || cat; } \
    | sed -E 's/.*\[([0-9]+)\] .*/\1/' \
    | head -1
}

# go2rtc.yaml is now STATIC: cam-outside / cam-inside are passive RTSP-ingest
# streams, and a SINGLE external ffmpeg (scripts/dual-cam-capture.sh) opens BOTH
# cameras and publishes into them. So this script no longer pins indices into
# the config — it only PREFLIGHTS that the right cameras are present (failing
# loudly if not), then materialises the static config from the template.
# Camera selection itself happens inside dual-cam-capture.sh:
#   * default            → the two AnkerWork cameras
#   * EDGE_CAM_*_IDX env  → two forced literal indices (inherited via `just up`)
#   * CAMERA_TEST=true    → Brio + built-in MacBook (resolved by name)
if [[ -n "${EDGE_CAM_OUTSIDE_IDX:-}" && -n "${EDGE_CAM_INSIDE_IDX:-}" ]]; then
  echo "→ cameras forced via EDGE_CAM_* (outside=$EDGE_CAM_OUTSIDE_IDX inside=$EDGE_CAM_INSIDE_IDX)" >&2

elif [[ "${CAMERA_TEST:-}" == "true" ]]; then
  bri="$(_idx_by_name 'brio' || true)"
  mb="$(_idx_by_name 'macbook' 'desk view' || true)"
  if [[ -z "$bri" || -z "$mb" ]]; then
    echo "error: CAMERA_TEST=true needs the Brio + MacBook cameras —" >&2
    echo "       found brio='${bri:-none}'  macbook='${mb:-none}'." >&2
    printf '       %s\n' "$VIDEO_BLOCK" >&2
    exit 1
  fi
  echo "→ CAMERA_TEST mode: dual-cam-capture.sh will open Brio($bri) + MacBook($mb)" >&2

else
  # Default: require the two AnkerWork cameras. dual-cam-capture.sh resolves the
  # actual indices inside its own (single) capture process — the only context
  # where the index→device mapping is meaningful. Here we just count them so the
  # operator fails loudly if fewer than two are plugged in.
  ANKER_COUNT=$(printf '%s\n' "$VIDEO_BLOCK" | grep -ciE '\[[0-9]+\][[:space:]].*anker' || true)
  if [[ "${ANKER_COUNT:-0}" -lt 2 ]]; then
    echo "error: this demo runs on the AnkerWork cameras ONLY — found ${ANKER_COUNT:-0}." >&2
    echo "       Plug in two AnkerWork webcams, OR:" >&2
    echo "         CAMERA_TEST=true just up          # test on Brio + MacBook built-in" >&2
    echo "         EDGE_CAM_OUTSIDE_IDX=<n> EDGE_CAM_INSIDE_IDX=<m> just up   # force indices" >&2
    echo "       (visible AVFoundation video devices follow)" >&2
    printf '       %s\n' "$VIDEO_BLOCK" >&2
    exit 1
  fi
  echo "→ AnkerWork cameras present ($ANKER_COUNT); dual-cam-capture.sh opens BOTH in one process" >&2
fi

# Static config — no per-stream substitution anymore.
cp "$TEMPLATE" "$OUT"
echo "  ✓ wrote $OUT" >&2
