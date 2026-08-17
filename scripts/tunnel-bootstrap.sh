#!/usr/bin/env bash
# One-time tunnel bootstrap. Installs the DNS resolver override and the
# static /etc/hosts entry for the (non-wildcard) telemetry endpoint.
# Requires sudo once; daily `just up`/`just down` then run without sudo.
#
# Run this whenever the host machine is reset or after a macOS upgrade
# blew away /etc/resolver/. Safe to re-run (idempotent).

set -euo pipefail

RESOLVER_FILE="/etc/resolver/us2.cloud.expanso.io"
HOSTS_MARKER="# >>> demo-drone-detection tunnel (managed) >>>"
HOSTS_END="# <<< demo-drone-detection tunnel (managed) <<<"
TELEMETRY_HOST="telemetry.us1.cloud.expanso.io"

echo "→ This bootstrap needs sudo to write /etc/resolver/ and /etc/hosts."
echo "  After this one-time run, 'just up' and 'just down' are sudo-free."
sudo -v

# /etc/resolver/us2.cloud.expanso.io — route *.us2.cloud.expanso.io
# lookups to our local dnsmasq on 127.0.0.1:5354. Note: macOS resolver
# files do NOT match the bare parent — they match the domain and its
# subdomains, which is exactly what we want.
sudo mkdir -p /etc/resolver
sudo tee "$RESOLVER_FILE" > /dev/null <<EOF
# Managed by demo-drone-detection. Routes *.us2.cloud.expanso.io lookups
# to a local dnsmasq, which is started by 'just up' and answers 127.0.0.1
# for any subdomain. The local SSH tunnel + sniproxy on hetzner-main then
# carry traffic out via clean egress.
nameserver 127.0.0.1
port 5354
EOF
echo "  ✓ wrote $RESOLVER_FILE"

# /etc/hosts — pin the (stable) telemetry hostname to 127.0.0.1.
# Wrapped in marker comments so tunnel-bootstrap can be re-run idempotently
# and the next maintainer knows where this came from.
if grep -qF "$HOSTS_MARKER" /etc/hosts; then
  echo "  ✓ /etc/hosts entry already present"
else
  sudo tee -a /etc/hosts > /dev/null <<EOF

$HOSTS_MARKER
127.0.0.1 $TELEMETRY_HOST
$HOSTS_END
EOF
  echo "  ✓ added /etc/hosts entry for $TELEMETRY_HOST"
fi

# Flush macOS DNS cache so the new resolver takes effect immediately.
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder 2>/dev/null || true
echo "  ✓ flushed DNS cache"

echo
echo "Bootstrap complete. Now run: just up"
