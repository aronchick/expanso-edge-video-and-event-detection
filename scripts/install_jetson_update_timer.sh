#!/usr/bin/env bash
# Install the expanso-edge auto-updater (script + systemd service + timer)
# on the Jetson via SSH. Idempotent. Run from the Mac.
#
# Usage:
#   JETSON_HOST=jetson ./scripts/install_jetson_update_timer.sh [flags]
#
# Flags:
#   --run-now   Run the updater immediately (synchronous, ~60-90s).
#   --enable    Enable the nightly timer.
#   (none)      Install but leave timer DISABLED.  Recommended pre-demo.
#
# Files installed on the Jetson:
#   /usr/local/sbin/jetson_update_expanso_edge.sh        (root, 0755)
#   /etc/systemd/system/armyx-edge-update.service        (root, 0644)
#   /etc/systemd/system/armyx-edge-update.timer          (root, 0644)
#   /var/log/expanso-edge-update.log                     (root, 0644)

set -euo pipefail

JETSON_HOST="${JETSON_HOST:-jetson}"
RUN_NOW=0
ENABLE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-now) RUN_NOW=1 ;;
    --enable)  ENABLE=1 ;;
    --help|-h) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UPDATER="$REPO_ROOT/scripts/jetson_update_expanso_edge.sh"
SERVICE="$REPO_ROOT/scripts/jetson-systemd/armyx-edge-update.service"
TIMER="$REPO_ROOT/scripts/jetson-systemd/armyx-edge-update.timer"

for f in "$UPDATER" "$SERVICE" "$TIMER"; do
  [[ -f "$f" ]] || { echo "missing: $f"; exit 1; }
done

echo "=== copying updater + units to $JETSON_HOST ==="
scp -q "$UPDATER" "$JETSON_HOST:/tmp/jetson_update_expanso_edge.sh"
scp -q "$SERVICE" "$JETSON_HOST:/tmp/armyx-edge-update.service"
scp -q "$TIMER"   "$JETSON_HOST:/tmp/armyx-edge-update.timer"

ssh "$JETSON_HOST" 'set -e
  sudo install -m 0755 /tmp/jetson_update_expanso_edge.sh /usr/local/sbin/jetson_update_expanso_edge.sh
  sudo install -m 0644 /tmp/armyx-edge-update.service /etc/systemd/system/armyx-edge-update.service
  sudo install -m 0644 /tmp/armyx-edge-update.timer   /etc/systemd/system/armyx-edge-update.timer
  rm -f /tmp/jetson_update_expanso_edge.sh /tmp/armyx-edge-update.service /tmp/armyx-edge-update.timer
  sudo systemctl daemon-reload
  sudo touch /var/log/expanso-edge-update.log
  sudo chmod 0644 /var/log/expanso-edge-update.log
  echo "OK installed: /usr/local/sbin/jetson_update_expanso_edge.sh"
  echo "OK installed: /etc/systemd/system/armyx-edge-update.{service,timer}"
'

if [[ "$RUN_NOW" -eq 1 ]]; then
  echo
  echo "=== running update NOW (synchronous; can take 60-120s) ==="
  # systemctl start of a Type=oneshot blocks until the service exits.
  ssh "$JETSON_HOST" 'sudo systemctl start armyx-edge-update.service' \
    && echo "service exit: 0" \
    || echo "service exit: non-zero (rolled back)"
  echo
  echo "=== last 60 lines of /var/log/expanso-edge-update.log ==="
  ssh "$JETSON_HOST" 'sudo tail -60 /var/log/expanso-edge-update.log'
fi

if [[ "$ENABLE" -eq 1 ]]; then
  ssh "$JETSON_HOST" 'sudo systemctl enable --now armyx-edge-update.timer'
  echo
  echo "✓ Timer enabled. Updates will run nightly at 03:00 (with up to 15min jitter)."
  echo "  Persistent=true means missed runs catch up on next boot."
  echo "  Inspect:  ssh $JETSON_HOST 'systemctl list-timers armyx-edge-update'"
else
  echo
  echo "Timer is INSTALLED but NOT ENABLED. To enable nightly updates:"
  echo "  ssh $JETSON_HOST 'sudo systemctl enable --now armyx-edge-update.timer'"
fi
