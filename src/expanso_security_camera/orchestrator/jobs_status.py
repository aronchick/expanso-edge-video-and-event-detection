"""Expanso job inventory surface.

Per DEMO_UI_SPEC.md §3.3. The dashboard's EXPANSO PLATFORM tile polls
/jobs to render four status dots. We try to shell out to `expanso-cli`
for real status; if it isn't on PATH or returns nothing, fall back to
`pgrep` for the local process-backed jobs (sensor-north, sensor-south,
fusion-node) so the panel reflects what is ACTUALLY running on the box
rather than a synthetic guess. The cloud-side `armyx-tech-event-archive`
pipeline is reported as `pending` if the CLI can't reach Expanso Cloud.

This honesty matters for DEMO_SCRIPT.md Beat 0: judges literally watch
the operator deploy four jobs and the panel must transition 0/4 → 4/4
in real time. If the synthetic fallback lied "running" before the deploy
ran, Beat 0 would be a no-op visually. The fallback now reflects reality
(processes alive on the host) without claiming a cloud state we can't
verify.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time

EXPECTED_JOBS = [
    # The fusion-node is the local FastAPI process: event store + cross-sensor
    # correlator + WebSocket + dashboard backend. NOT the cluster orchestrator
    # (that's Expanso Cloud). Renamed from "orchestrator" so the demo script's
    # vocabulary doesn't fight Expanso's term-of-art.
    {"name": "fusion-node", "type": "ops", "role": "fusion + dashboard"},
    {"name": "sensor-north", "type": "ops", "role": "edge sensor"},
    {"name": "sensor-south", "type": "ops", "role": "edge sensor"},
    {"name": "armyx-tech-event-archive", "type": "pipeline", "role": "S3 archive"},
]

# pgrep patterns for each process-backed job. The orchestrator process registers
# as `edge-orchestrator`; sensors as `edge-sensor --node-id <name>`. Anchored on
# `--node-id <name>` so we don't false-match a sensor for the orchestrator.
PGREP_PATTERNS: dict[str, str] = {
    "fusion-node": r"edge-orchestrator",
    "sensor-north": r"edge-sensor.*--node-id\s+sensor-north",
    "sensor-south": r"edge-sensor.*--node-id\s+sensor-south",
}


class JobsStatus:
    """Caches the latest job status so we don't shell out on every poll."""

    def __init__(self, refresh_sec: float = 2.0) -> None:
        self._refresh_sec = refresh_sec
        self._last_refresh = 0.0
        self._cached: list[dict] = self._fetch()

    def get(self) -> list[dict]:
        now = time.time()
        if now - self._last_refresh > self._refresh_sec:
            self._cached = self._fetch()
            self._last_refresh = now
        return self._cached

    def _fetch(self) -> list[dict]:
        # Start with the expected inventory, then layer reality on top from
        # two independent sources: pgrep for host processes, expanso-cli for
        # the cloud-side archive pipeline.
        by_name: dict[str, dict] = {j["name"]: dict(j) for j in EXPECTED_JOBS}

        # Step 1: pgrep for the three process-backed jobs.
        for name, pattern in PGREP_PATTERNS.items():
            by_name[name]["status"] = "running" if _pgrep(pattern) else "failed"

        # Step 2: expanso-cli job list for cloud-side jobs (S3 archive, etc.).
        # If the CLI is unauthenticated or unreachable, leave whatever we
        # already have for the archive entry as `pending`.
        by_name["armyx-tech-event-archive"].setdefault("status", "pending")
        cloud_jobs = self._fetch_cloud_jobs()
        for item in cloud_jobs:
            name = item["name"]
            if name in by_name:
                by_name[name]["status"] = item["status"]
            else:
                by_name[name] = {
                    "name": name,
                    "type": item.get("type", "pipeline"),
                    "role": "extra",
                    "status": item["status"],
                }

        # Anything still without a status (shouldn't happen) defaults to pending.
        for job in by_name.values():
            job.setdefault("status", "pending")
        return list(by_name.values())

    def _fetch_cloud_jobs(self) -> list[dict]:
        """Return [{name,type,status}] from `expanso-cli job list`, or []."""
        cli = (
            "expanso-cli"
            if shutil.which("expanso-cli")
            else ("expanso" if shutil.which("expanso") else None)
        )
        if cli is None:
            return []
        # The current Expanso CLI uses `--format json`, not `--output json`.
        try:
            result = subprocess.run(
                [cli, "job", "list", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []
            data = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            return []

        items = data if isinstance(data, list) else data.get("jobs", [])
        normalized: list[dict] = []
        for item in items:
            spec = item.get("spec", item)
            status = item.get("status", {})
            name = spec.get("name") or item.get("Name") or item.get("name") or ""
            if not name:
                continue
            type_ = spec.get("type") or item.get("Type") or item.get("type") or "pipeline"
            # New API shape: status.state.state_type. Fall back to flat fields.
            state = (
                status.get("state", {}).get("state_type")
                if isinstance(status.get("state"), dict)
                else None
            )
            if state is None:
                state = item.get("Status") or item.get("status") or "running"
            normalized.append(
                {"name": name, "type": str(type_).lower(), "status": str(state).lower()}
            )
        return normalized


def _pgrep(pattern: str) -> bool:
    """True if `pgrep -f <pattern>` finds at least one match."""
    if shutil.which("pgrep") is None:
        return False
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=1,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return False
