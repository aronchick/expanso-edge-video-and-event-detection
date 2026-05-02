"""SSH-based control of the Jetson's Wi-Fi radio.

The demo's Beat A and Beat C hinge on radio-wise disconnecting the
Jetson from cloud.expanso.io and AWS, then bringing it back. Doing this
from the dashboard is much cleaner than yanking a cable on stage:

  - F1 in the dashboard => POST /demo/wan-down => ssh jetson nmcli radio
    wifi off => Jetson loses internet, control plane goes silent, S3
    archive pipeline buffers locally.
  - F2 => ssh jetson nmcli radio wifi on => reconnect, drain.

The Mac-Jetson LAN is wired through the PoE switch and is not affected
by the Wi-Fi toggle, so the dashboard keeps painting throughout.

Safety: uses subprocess.run with an argv list (never a shell string),
so the JETSON_HOST env value cannot inject a shell command. shlex.quote
is also applied to the radio interface name as belt-and-suspenders.

Failure modes:
  - JETSON_HOST not set, or ssh fails => controller reports an error and
    the cosmetic in-memory flag still flips so the demo can fall back to
    "fake" mode (useful when the operator is rehearsing on a laptop).
  - sudo needs a password => the Jetson sudoers must grant NOPASSWD for
    /usr/bin/nmcli for the SSH user. Bootstrap doc covers it.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class WanResult:
    ok: bool
    cosmetic_only: bool  # True if the SSH call wasn't attempted/failed but flag was flipped
    detail: str


class JetsonWanController:
    def __init__(
        self,
        jetson_host: str | None = None,
        ssh_timeout_sec: float = 4.0,
        radio_iface: str = "wifi",
    ) -> None:
        # Empty string means "fake mode" — useful for laptop dev where there
        # is no Jetson to ssh into.
        self.jetson_host = (
            jetson_host if jetson_host is not None else os.environ.get("ARMYX_JETSON_HOST", "")
        )
        self.ssh_timeout_sec = ssh_timeout_sec
        self.radio_iface = radio_iface

    async def set_wan(self, up: bool) -> WanResult:
        if not self.jetson_host:
            return WanResult(
                ok=True,
                cosmetic_only=True,
                detail="ARMYX_JETSON_HOST not set; flag-only toggle (laptop dev mode)",
            )

        action = "on" if up else "off"
        # nmcli is on the Jetson NOPASSWD allowlist; see scripts/setup_jetson_lan.sh.
        remote_cmd = f"sudo /usr/bin/nmcli radio {shlex.quote(self.radio_iface)} {action}"
        ssh_argv = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={int(self.ssh_timeout_sec)}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            self.jetson_host,
            remote_cmd,
        ]

        def _run() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                ssh_argv,
                capture_output=True,
                timeout=self.ssh_timeout_sec + 2.0,
                check=False,
            )

        try:
            result = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            return WanResult(
                ok=False,
                cosmetic_only=True,
                detail=f"ssh timed out after {self.ssh_timeout_sec}s",
            )
        except FileNotFoundError:
            return WanResult(ok=False, cosmetic_only=True, detail="ssh not on PATH")

        if result.returncode == 0:
            return WanResult(
                ok=True,
                cosmetic_only=False,
                detail=f"radio {self.radio_iface} {action}",
            )

        err = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
        )
        return WanResult(
            ok=False,
            cosmetic_only=True,
            detail=f"ssh exit {result.returncode}: {err[:200]}",
        )
