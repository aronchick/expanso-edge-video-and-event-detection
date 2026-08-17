#!/usr/bin/env bash
# Preflight for the DIRECT path to Expanso Cloud (no jump box).
#
# `just up` reaches cloud.expanso.io straight out of this machine: NATS on
# 4222, cluster API on 9010, OTLP on 4318. That works on every network we've
# measured — the egress tunnel (scripts/tunnel-up.sh) is now an opt-in escape
# hatch for venues that genuinely block non-443 outbound, not the default.
#
# The one way direct egress silently breaks: leftover artifacts from
# `just tunnel-bootstrap`. That bootstrap points *.us2.cloud.expanso.io at a
# local dnsmasq (127.0.0.1) so the SSH forward can carry the traffic while TLS
# still validates against the real cluster cert. Those artifacts are permanent
# and survive the tunnel dying — at which point the cluster hostname resolves
# to a loopback port with nothing behind it and every cloud call hangs, with no
# obvious clue why. Catch that here and name the fix.

set -euo pipefail

RESOLVER_FILE="/etc/resolver/us2.cloud.expanso.io"
HOSTS_MARKER="# >>> demo-drone-detection tunnel (managed) >>>"
STATE_DIR=".demo-state"
CONN_CONF="$STATE_DIR/expanso-edge/config.d/50-connection.yaml"

hijacked=0
[[ -f "$RESOLVER_FILE" ]] && hijacked=1
grep -qF "$HOSTS_MARKER" /etc/hosts 2>/dev/null && hijacked=1

if [[ "$hijacked" == "1" ]]; then
  # Only a hard error if the tunnel that justifies the hijack isn't running.
  if pgrep -f "dnsmasq.*dnsmasq-tunnel.conf" > /dev/null 2>&1; then
    echo "  ✓ egress via tunnel (dnsmasq + DNS override active)"
    exit 0
  fi
  cat >&2 <<'EOF'
✗ Leftover tunnel DNS override, but no tunnel running.

  /etc/resolver/us2.cloud.expanso.io (and/or the managed /etc/hosts block)
  points the Expanso cluster at 127.0.0.1 for the SSH tunnel. With the tunnel
  down, the cluster hostname resolves to a dead loopback port and every call
  to cloud.expanso.io hangs.

  Fix (one time, needs sudo):   just tunnel-unbootstrap

  Then `just up` reaches the cloud directly. Only run `just tunnel-bootstrap`
  again if you hit a venue that blocks outbound 4222/9010.
EOF
  exit 1
fi

# Soft reachability probe against the cluster this data-dir is registered to.
# Never fatal: a booth network can be slow, and the daemon retries on its own.
if [[ -f "$CONN_CONF" ]]; then
  cluster_host=$(awk -F'[/:]' '/address:[[:space:]]*nats:/ {print $4; exit}' "$CONN_CONF")
  if [[ -n "${cluster_host:-}" ]]; then
    if timeout 8 bash -c "</dev/tcp/$cluster_host/4222" 2>/dev/null; then
      echo "  ✓ direct egress to $cluster_host:4222 (no tunnel needed)"
    else
      echo "  ⚠ can't reach $cluster_host:4222 directly."
      echo "    If this venue blocks non-443 outbound: just tunnel-bootstrap && just tunnel-up"
      echo "    Continuing — expanso-edge retries in the background."
    fi
  fi
fi

exit 0
