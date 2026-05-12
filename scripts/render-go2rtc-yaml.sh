#!/usr/bin/env bash
# Render go2rtc.yaml from go2rtc.yaml.template by resolving the two
# AnkerWork C310 Webcam AVFoundation indices RIGHT NOW. macOS reorders
# webcam indices between processes, so static index pins (e.g. always
# `-i "2"`) drift and end up streaming the Brio or MacBook instead of
# the Ankers. Resolving at startup keeps the window of vulnerability
# down to the few seconds between this script finishing and go2rtc
# spawning its ffmpeg children.
#
# Exits non-zero if fewer than 2 Anker cameras are visible to ffmpeg.

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

# Extract just the "[N] AnkerWork C310 Webcam" entries' indices.
ANKER_INDICES=( $(printf '%s\n' "$VIDEO_BLOCK" \
  | grep -E '\[[0-9]+\] AnkerWork C310 Webcam' \
  | sed -E 's/.*\[([0-9]+)\] .*/\1/') )

if [[ ${#ANKER_INDICES[@]} -lt 2 ]]; then
  echo "error: expected ≥2 AnkerWork C310 Webcam devices, found ${#ANKER_INDICES[@]}" >&2
  echo "       (visible AVFoundation video devices follow)" >&2
  printf '       %s\n' "$VIDEO_BLOCK" >&2
  exit 1
fi

OUTSIDE_IDX="${ANKER_INDICES[0]}"
INSIDE_IDX="${ANKER_INDICES[1]}"

echo "→ AnkerWork indices resolved: cam-outside=$OUTSIDE_IDX  cam-inside=$INSIDE_IDX" >&2

sed -e "s/__ANKER_OUTSIDE_IDX__/$OUTSIDE_IDX/" \
    -e "s/__ANKER_INSIDE_IDX__/$INSIDE_IDX/" \
    "$TEMPLATE" > "$TMP"
mv "$TMP" "$OUT"
echo "  ✓ wrote $OUT" >&2
