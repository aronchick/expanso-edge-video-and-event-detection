#!/usr/bin/env bash
# Bring up the SSH tunnel through hetzner-main + local dnsmasq.
# Called by `just up` before expanso-edge starts.
#
# Layout:
#   - dnsmasq (this Mac, 127.0.0.1:5354) wildcards *.us2.cloud.expanso.io → 127.0.0.1
#   - autossh keeps an SSH tunnel alive across laptop sleep / network
#     changes, forwarding 4222/9010 → sniproxy on hetzner-main (which
#     reads SNI and dials the real cluster) and 4318 → the static
#     telemetry host
#   - End-to-end TLS validates against the real cluster cert; no skip-verify
#
# autossh (not plain ssh ControlMaster) because this Mac sleeps
# constantly — a bare ssh -fNM master dies on the first sleep/network
# blip and never comes back. autossh respawns it.

set -euo pipefail

STATE_DIR=".demo-state"
DNSMASQ_PID="$STATE_DIR/dnsmasq.pid"
DNSMASQ_LOG="$STATE_DIR/dnsmasq.log"
DNSMASQ_CONF="scripts/dnsmasq-tunnel.conf"
TUNNEL_PID="$STATE_DIR/tunnel.pid"
TUNNEL_LOG="$STATE_DIR/tunnel.log"
RESOLVER_FILE="/etc/resolver/us2.cloud.expanso.io"
# Real hostname, not an ssh_config alias. This used to be "hetzner-main",
# which resolved only via a Host block in ~/.ssh/config.d/ — when that
# directory went away the tunnel broke with "Could not resolve hostname
# hetzner-main" and took the whole demo down with it. Depend on DNS that
# exists everywhere instead. Override with TUNNEL_SSH_TARGET for another
# jump box.
SSH_TARGET="${TUNNEL_SSH_TARGET:-daaronch@hetzner.busted.dev}"

mkdir -p "$STATE_DIR"

# Preflight: bootstrap must have run.
if [[ ! -f "$RESOLVER_FILE" ]]; then
  echo "✗ $RESOLVER_FILE missing — run 'just tunnel-bootstrap' once" >&2
  exit 1
fi
if ! grep -qF "demo-drone-detection tunnel (managed)" /etc/hosts; then
  echo "✗ /etc/hosts marker missing — run 'just tunnel-bootstrap' once" >&2
  exit 1
fi

# Preflight: binaries present.
DNSMASQ_BIN="$(command -v dnsmasq || true)"
if [[ -z "$DNSMASQ_BIN" ]]; then
  echo "✗ dnsmasq not on PATH — 'brew install dnsmasq'" >&2
  exit 1
fi
AUTOSSH_BIN="$(command -v autossh || true)"
if [[ -z "$AUTOSSH_BIN" ]]; then
  echo "✗ autossh not on PATH — 'brew install autossh'" >&2
  exit 1
fi

# Start dnsmasq (foreground, backgrounded with nohup — same pattern as the
# rest of the justfile). bind-interfaces in the config ensures we only
# listen on 127.0.0.1:5354 and don't clash with anything else.
if [[ -f "$DNSMASQ_PID" ]] && kill -0 "$(cat "$DNSMASQ_PID")" 2>/dev/null; then
  echo "  ✓ dnsmasq already running (PID $(cat "$DNSMASQ_PID"))"
else
  nohup "$DNSMASQ_BIN" --conf-file="$DNSMASQ_CONF" --keep-in-foreground \
    > "$DNSMASQ_LOG" 2>&1 &
  echo $! > "$DNSMASQ_PID"
  sleep 0.3
  if ! kill -0 "$(cat "$DNSMASQ_PID")" 2>/dev/null; then
    echo "✗ dnsmasq failed to start; see $DNSMASQ_LOG" >&2
    exit 1
  fi
  echo "  ✓ dnsmasq up (PID $(cat "$DNSMASQ_PID")) — 127.0.0.1:5354"
fi

# Verify the resolver actually returns 127.0.0.1 for a cluster lookup.
# Catches a stale resolver cache or a misconfigured dnsmasq.
test_host="probe.us2.cloud.expanso.io"
resolved=$(dscacheutil -q host -a name "$test_host" 2>&1 | awk '/^ip_address/ {print $2; exit}')
if [[ "$resolved" != "127.0.0.1" ]]; then
  echo "  ⚠ resolver returned '$resolved' for $test_host (expected 127.0.0.1)"
  echo "    macOS may have a stale cache — 'sudo dscacheutil -flushcache'"
fi

# Start autossh with three port-forwards.
#   - 4222/9010 → sniproxy on hetzner-main (SNI-routes to the real cluster)
#   - 4318      → telemetry.us1.cloud.expanso.io (resolved by hetzner-main)
# -M 0 disables autossh's own monitoring port; we rely on SSH's
# ServerAlive keepalives instead (the modern recommended setup).
# ExitOnForwardFailure makes a stuck local port fail fast so autossh
# retries cleanly rather than running a half-open tunnel.
if [[ -f "$TUNNEL_PID" ]] && kill -0 "$(cat "$TUNNEL_PID")" 2>/dev/null; then
  echo "  ✓ autossh tunnel already running (PID $(cat "$TUNNEL_PID"))"
else
  rm -f "$TUNNEL_PID"
  # NOTE: not autossh -f. autossh's own daemonize is broken on recent
  # macOS (the parent exits 0 but no daemon survives). Background it with
  # nohup + & instead — same pattern the rest of this justfile uses for
  # every process — and capture the shell PID, which IS the autossh
  # watchdog (it then manages its own ssh child + respawn-on-sleep).
  AUTOSSH_GATETIME=0 nohup \
  "$AUTOSSH_BIN" -M 0 -N \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=accept-new \
    -L 127.0.0.1:4222:127.0.0.1:14222 \
    -L 127.0.0.1:9010:127.0.0.1:19010 \
    -L 127.0.0.1:4318:telemetry.us1.cloud.expanso.io:4318 \
    "$SSH_TARGET" > "$TUNNEL_LOG" 2>&1 &
  echo $! > "$TUNNEL_PID"
  # Give autossh a beat to establish the first connection, then verify
  # the listeners are actually up.
  for _ in $(seq 1 10); do
    if lsof -nP -iTCP@127.0.0.1:9010 -sTCP:LISTEN >/dev/null 2>&1; then break; fi
    sleep 0.5
  done
  if ! lsof -nP -iTCP@127.0.0.1:9010 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "✗ tunnel forwards not listening; see $TUNNEL_LOG" >&2
    exit 1
  fi
  echo "  ✓ autossh tunnel up via $SSH_TARGET (PID $(cat "$TUNNEL_PID" 2>/dev/null || echo '?')) — fwd 4222, 9010, 4318"
fi
