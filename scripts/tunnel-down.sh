#!/usr/bin/env bash
# Tear down the autossh tunnel + local dnsmasq. Called by `just down`.
# Idempotent: a noop if nothing is running.

set -euo pipefail

STATE_DIR=".demo-state"
DNSMASQ_PID="$STATE_DIR/dnsmasq.pid"
TUNNEL_PID="$STATE_DIR/tunnel.pid"

# Stop autossh via pidfile. Killing autossh also reaps its child ssh
# (autossh installs a SIGTERM handler that tears the tunnel down).
if [[ -f "$TUNNEL_PID" ]]; then
  pid=$(cat "$TUNNEL_PID")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null && echo "  ✓ killed autossh tunnel (PID $pid)"
  fi
  rm -f "$TUNNEL_PID"
fi
# Belt-and-suspenders: any autossh/ssh still pointed at our forwards.
pkill -f "autossh.*127.0.0.1:9010:127.0.0.1:19010" 2>/dev/null || true
pkill -f "ssh.*127.0.0.1:9010:127.0.0.1:19010" 2>/dev/null || true

# Stop dnsmasq via pidfile.
if [[ -f "$DNSMASQ_PID" ]]; then
  pid=$(cat "$DNSMASQ_PID")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null && echo "  ✓ killed dnsmasq (PID $pid)"
  fi
  rm -f "$DNSMASQ_PID"
fi
# Belt-and-suspenders: anything still bound to our dnsmasq config file.
pkill -f "dnsmasq.*dnsmasq-tunnel.conf" 2>/dev/null || true
