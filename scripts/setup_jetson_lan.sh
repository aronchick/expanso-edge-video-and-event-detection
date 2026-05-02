#!/usr/bin/env bash
# Configure the Jetson as the LAN's DHCP/DNS host on eth0, while leaving
# wlan0 free as the WAN path to Expanso Cloud + AWS (toggleable for Beat 5A/C).
#
# Run this on the Jetson, once. Idempotent.
#
# What it touches:
#   - apt: dnsmasq, network-manager (already on JetPack normally)
#   - /etc/dnsmasq.d/armyx-tech.conf  (DHCP+DNS for 192.168.50.0/24)
#   - /etc/NetworkManager/system-connections/armyx-tech-lan.nmconnection
#         (static 192.168.50.1/24 on eth0, no gateway, no DNS — wlan0 is WAN)
#   - /etc/sudoers.d/armyx-tech-nmcli  (NOPASSWD nmcli for the SSH user)
#   - systemd: enables dnsmasq, restarts NetworkManager
#
# After this, F1/F2 from the Mac dashboard can do:
#     ssh nvidia@jetson sudo /usr/bin/nmcli radio wifi off|on
# without a password prompt.

set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "This script must be run as root on the Jetson (use sudo)."
  exit 1
fi

LAN_IFACE="${LAN_IFACE:-eth0}"
WAN_IFACE="${WAN_IFACE:-wlan0}"
LAN_IP="192.168.50.1"
DHCP_RANGE="192.168.50.20,192.168.50.50,12h"
SSH_USER="${SUDO_USER:-nvidia}"

# ---------- packages ----------
if ! command -v dnsmasq >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y dnsmasq
fi

# ---------- dnsmasq config ----------
cat > /etc/dnsmasq.d/armyx-tech.conf <<EOF
# armyx-tech edge ISR demo — LAN-only DNS+DHCP for ${LAN_IFACE}.
# Important: NetworkManager also runs a tiny dnsmasq for shared connections
# on some JetPacks; we bind to ${LAN_IFACE} only and never to wlan0.
interface=${LAN_IFACE}
bind-interfaces
listen-address=${LAN_IP}
no-dhcp-interface=${WAN_IFACE},lo

# DHCP for the demo subnet. Cameras have static addresses (.11/.12) and
# the Mac gets a stable lease from the host-level reservation below.
dhcp-range=${DHCP_RANGE}

# Static reservations — keep the Mac and the cameras at known IPs so the
# RTSP URLs and dashboard config don't drift. Edit the MAC addresses here
# for your hardware (find with arp -a after first plug-in).
# dhcp-host=AA:BB:CC:DD:EE:FF,192.168.50.30,mac
# dhcp-host=BB:CC:DD:EE:FF:AA,192.168.50.11,reolink-north
# dhcp-host=CC:DD:EE:FF:AA:BB,192.168.50.12,reolink-south

# DNS: forward upstream queries via the Jetson's WAN-side resolver.
# The LAN doesn't need internet routing through us; each host has its
# own Wi-Fi. But local hostname resolution (jetson.local etc.) is nice.
domain=armyx.local
local=/armyx.local/
expand-hosts
EOF

# ---------- NetworkManager static profile ----------
NM_FILE="/etc/NetworkManager/system-connections/armyx-tech-lan.nmconnection"
cat > "$NM_FILE" <<EOF
[connection]
id=armyx-tech-lan
type=ethernet
interface-name=${LAN_IFACE}
autoconnect=true

[ethernet]

[ipv4]
method=manual
addresses=${LAN_IP}/24
# NO gateway, NO DNS — wlan0 is the WAN. This keeps eth0 LAN-only so the
# default route doesn't try to leave through the cameras.

[ipv6]
method=ignore
EOF
chmod 600 "$NM_FILE"

# ---------- sudoers for nmcli (NOPASSWD) ----------
cat > /etc/sudoers.d/armyx-tech-nmcli <<EOF
# Allow the SSH user to toggle Wi-Fi without a password — this is what
# the Mac orchestrator's F1/F2 uses to drive Beat 5A/C of the demo.
${SSH_USER} ALL=(root) NOPASSWD: /usr/bin/nmcli radio wifi *
${SSH_USER} ALL=(root) NOPASSWD: /usr/bin/nmcli device status
EOF
chmod 0440 /etc/sudoers.d/armyx-tech-nmcli
visudo -cf /etc/sudoers.d/armyx-tech-nmcli >/dev/null

# ---------- enable services ----------
systemctl enable dnsmasq
systemctl restart dnsmasq
nmcli connection reload
nmcli connection up armyx-tech-lan || true

echo ""
echo "=== Jetson LAN setup complete ==="
echo "  ${LAN_IFACE}  : ${LAN_IP}/24 (DHCP+DNS host)"
echo "  ${WAN_IFACE} : retains current Wi-Fi profile (toggleable WAN)"
echo "  sudoers    : ${SSH_USER} can run nmcli radio wifi {on,off} NOPASSWD"
echo ""
echo "Verify from the Mac:"
echo "  ssh ${SSH_USER}@${LAN_IP} sudo /usr/bin/nmcli radio wifi"
