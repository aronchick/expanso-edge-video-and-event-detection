#!/usr/bin/env bash
# Update expanso-edge to the latest version on this Jetson, with backup +
# rollback. Runs as root. Designed to be invoked by a systemd oneshot
# service (armyx-edge-update.service) on a nightly timer, or manually.
#
# Strategy:
#   1. Capture pre-update state (binary, version, NATS cluster URL).
#   2. Stop the service cleanly.
#   3. Run Expanso's official installer (USE_SUDO=true so it can write
#      /usr/local/bin/expanso-edge).
#   4. Start the service.
#   5. Wait 30s, verify:
#        - service is active
#        - agent has logged a cluster connection
#        - cluster matches pre-update (same NATS URL)
#   6. On any failure: stop service, restore backup, restart, exit non-zero.
#
# All output goes to /var/log/expanso-edge-update.log AND stdout (so
# `journalctl -u armyx-edge-update.service` also shows everything).
#
# Idempotent: if the installer reports "already up to date", we still
# verify and exit 0; backups are pruned >30 days old.

set -uo pipefail

LOG=/var/log/expanso-edge-update.log
mkdir -p "$(dirname "$LOG")"
touch "$LOG"
exec > >(tee -a "$LOG") 2>&1

BIN=/usr/local/bin/expanso-edge
SERVICE=expanso-edge
BACKUP=""

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
log() { echo "[$(ts)] $*"; }

rollback_and_exit() {
  # Always unmask first so the service can actually be started.
  systemctl unmask --runtime "$SERVICE" >/dev/null 2>&1 || true
  systemctl reset-failed "$SERVICE" 2>/dev/null || true
  if [[ -n "$BACKUP" && -f "$BACKUP" ]]; then
    log "ROLLBACK: restoring $BACKUP -> $BIN"
    systemctl stop "$SERVICE" 2>/dev/null || true
    if cp -p "$BACKUP" "$BIN"; then
      log "binary restored"
    else
      log "ERROR: rollback copy failed; manual recovery needed"
    fi
    if systemctl start "$SERVICE"; then
      log "service restarted with old binary"
    else
      log "ERROR: rollback restart failed"
    fi
  else
    log "no backup to roll back to"
  fi
  exit 1
}

fail() { log "FATAL: $*"; rollback_and_exit; }

main() {
  log "=== expanso-edge update run ==="

  [[ "$EUID" -eq 0 ]] || { log "FATAL: must run as root"; exit 1; }
  [[ -x "$BIN" ]] || { log "FATAL: $BIN not present"; exit 1; }

  PRE_VERSION=$("$BIN" version 2>&1 | head -1)
  log "current version: $PRE_VERSION"

  PRE_NATS=$(journalctl -u "$SERVICE" --since "1 hour ago" --no-pager 2>/dev/null \
    | grep -oE 'server=nats://[^ ]+' | tail -1 | sed 's|server=||')
  log "current cluster NATS URL: ${PRE_NATS:-unknown}"

  BACKUP="/usr/local/bin/.expanso-edge.backup-$(date +%Y%m%d-%H%M%S)"
  cp -p "$BIN" "$BACKUP" || { log "FATAL: binary backup failed"; exit 1; }
  log "backed up binary to $BACKUP"

  # Mask BEFORE stop so systemd's Restart=always doesn't auto-respawn the
  # service during the install window.
  systemctl mask --runtime "$SERVICE" >/dev/null || fail "failed to mask $SERVICE"
  systemctl stop "$SERVICE" || fail "failed to stop $SERVICE"
  log "stopped + masked $SERVICE"

  # CRITICAL: expanso-edge spawns child pipeline executors (e.g.
  # `expanso-edge run pipeline.yaml`) that escape the service's cgroup.
  # `systemctl stop` doesn't kill them, leaving the BoltDB locked and
  # port 9010 bound when the new binary tries to start.
  # Explicitly nuke every expanso-edge process and wait for the lock to
  # actually clear before proceeding to the install.
  log "killing any orphaned expanso-edge child processes..."
  pkill -TERM -f /usr/local/bin/expanso-edge 2>/dev/null || true
  sleep 2
  pkill -KILL -f /usr/local/bin/expanso-edge 2>/dev/null || true
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! lsof /var/lib/expanso/edge/state/boltdb.db >/dev/null 2>&1; then
      log "BoltDB lock released after ${i}s"
      break
    fi
    sleep 1
    if [[ "$i" -eq 10 ]]; then
      fail "BoltDB lock still held after 10s of pkill — manual cleanup needed"
    fi
  done

  log "downloading and running official installer..."
  if ! curl -fsSL --max-time 60 https://get.expanso.io/edge/install.sh \
       | USE_SUDO=true bash; then
    systemctl unmask --runtime "$SERVICE" >/dev/null || true
    fail "installer failed (curl or bash exit non-zero)"
  fi
  log "installer finished"

  systemctl unmask --runtime "$SERVICE" >/dev/null || fail "failed to unmask $SERVICE"
  systemctl reset-failed "$SERVICE" 2>/dev/null || true
  systemctl start "$SERVICE" || fail "failed to start $SERVICE"
  log "started $SERVICE; waiting 30s for cluster reconnect..."
  sleep 30

  POST_VERSION=$("$BIN" version 2>&1 | head -1)
  log "post-update version: $POST_VERSION"

  systemctl is-active --quiet "$SERVICE" || fail "service not active after update"

  POST_NATS=$(journalctl -u "$SERVICE" --since "60 seconds ago" --no-pager 2>/dev/null \
    | grep -oE 'server=nats://[^ ]+' | tail -1 | sed 's|server=||')
  log "post-update cluster NATS URL: ${POST_NATS:-unknown}"

  [[ -n "$POST_NATS" ]] || fail "agent did not log a cluster connection within 30s"

  if [[ -n "$PRE_NATS" && "$PRE_NATS" != "$POST_NATS" ]]; then
    fail "agent jumped to a different cluster ($POST_NATS != $PRE_NATS)"
  fi

  log "=== UPDATE OK ==="
  log "  version : $PRE_VERSION  ->  $POST_VERSION"
  log "  cluster : $POST_NATS"
  log "  backup  : $BACKUP"

  # Prune backups older than 30 days.
  find /usr/local/bin -maxdepth 1 -name '.expanso-edge.backup-*' -mtime +30 -delete \
    2>/dev/null || true
}

main "$@"
