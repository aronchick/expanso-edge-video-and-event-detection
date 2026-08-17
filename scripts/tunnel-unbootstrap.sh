#!/usr/bin/env bash
# Reverse scripts/tunnel-bootstrap.sh: drop the DNS override and the managed
# /etc/hosts block so this machine resolves Expanso Cloud normally and talks
# to it directly.
#
# The bootstrap's artifacts are permanent while the tunnel is ephemeral, so a
# dead tunnel leaves DNS pointed at a loopback port with nothing behind it.
# This is the way out of that state. Safe to re-run.

set -euo pipefail

RESOLVER_FILE="/etc/resolver/us2.cloud.expanso.io"
HOSTS_MARKER="# >>> demo-drone-detection tunnel (managed) >>>"
HOSTS_END="# <<< demo-drone-detection tunnel (managed) <<<"

# Stop anything still holding the tunnel open first — otherwise dnsmasq keeps
# answering on 5354 for a resolver file we're about to delete.
./scripts/tunnel-down.sh

echo "→ Removing the tunnel DNS override (needs sudo once)."
sudo -v

if [[ -f "$RESOLVER_FILE" ]]; then
  sudo rm -f "$RESOLVER_FILE"
  echo "  ✓ removed $RESOLVER_FILE"
else
  echo "  ✓ $RESOLVER_FILE already gone"
fi

if grep -qF "$HOSTS_MARKER" /etc/hosts 2>/dev/null; then
  # Delete the marker block inclusive, plus the blank line bootstrap prepended.
  sudo sed -i '' "/^${HOSTS_MARKER}$/,/^${HOSTS_END}$/d" /etc/hosts
  echo "  ✓ removed managed /etc/hosts block"
else
  echo "  ✓ /etc/hosts block already gone"
fi

sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder 2>/dev/null || true
echo "  ✓ flushed DNS cache"

echo
echo "Direct egress restored. Run: just up"
