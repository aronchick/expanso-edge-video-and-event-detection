"""Expanso job inventory surface.

Per DEMO_UI_SPEC.md §3.3. The dashboard's EXPANSO PLATFORM tile polls
/jobs to render four status dots. We try to shell out to `expanso-cli`
for real status; if it isn't on PATH or returns nothing, fall back to
`pgrep` for the local process-backed jobs (sensor-north, sensor-south,
fusion-node) so the panel reflects what is ACTUALLY running on the box
rather than a synthetic guess. The cloud-side `event-archive` pipeline
(jobs/event-archive-job.yaml) is reported as `pending` if the CLI can't
reach Expanso Cloud or the job hasn't been deployed yet.

This honesty matters for DEMO_SCRIPT.md Beat 0: judges literally watch
the operator deploy four jobs and the panel must transition 0/4 → 4/4
in real time. If the synthetic fallback lied "running" before the deploy
ran, Beat 0 would be a no-op visually. The fallback now reflects reality
(processes alive on the host) without claiming a cloud state we can't
verify.
"""

from __future__ import annotations

import json
import re
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
    {"name": "event-archive", "type": "pipeline", "role": "S3 archive"},
]

# Patterns (Python re flavor) used by `_match_process()` against the
# output of `ps -axww -o pid=,command=`. We anchor on `.venv/bin/edge-<binary>`
# rather than just `edge-<binary>` to avoid false-matching unrelated shells
# whose cmdline mentions the string (e.g. this file open in an editor).
#
# Sensor patterns accept EITHER `--node-id sensor-X` (production mode, one
# process per sector) OR `--fake .* --multi` (laptop demo, one process
# emitting for both sectors).
PROCESS_PATTERNS: dict[str, re.Pattern[str]] = {
    "fusion-node": re.compile(r"\.venv/bin/edge-orchestrator(\s|$)"),
    "sensor-north": re.compile(
        r"\.venv/bin/edge-sensor.*"
        r"(--node-id[\s=]sensor-north|--fake.*--multi|--multi.*--fake)"
    ),
    "sensor-south": re.compile(
        r"\.venv/bin/edge-sensor.*"
        r"(--node-id[\s=]sensor-south|--fake.*--multi|--multi.*--fake)"
    ),
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
        # two independent sources: `ps` for host processes, expanso-cli for
        # the cloud-side archive pipeline.
        by_name: dict[str, dict] = {j["name"]: dict(j) for j in EXPECTED_JOBS}

        # Step 1: ps-based regex match for the three process-backed jobs.
        running_names = _running_local_jobs()
        for name in PROCESS_PATTERNS:
            by_name[name]["status"] = "running" if name in running_names else "failed"

        # Step 2: expanso-cli job list for cloud-side jobs (S3 archive, etc.).
        # If the CLI is unauthenticated or unreachable, leave whatever we
        # already have for the archive entry as `pending`.
        by_name["event-archive"].setdefault("status", "pending")
        cloud_jobs = self._fetch_cloud_jobs()
        for item in cloud_jobs:
            name = item["name"]
            if name in PROCESS_PATTERNS:
                # Process-backed job: the local check is authoritative. The
                # local process IS what serves the demo; a stopped Expanso-job
                # entry doesn't mean the process is dead. Skip the cloud
                # state to avoid overwriting "running" with stale "stopped".
                continue
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
                # 1s — when WAN is up this completes in <100ms; when WAN is
                # down we'd rather give up fast than have a long subprocess
                # hold its worker thread (and trigger any callers that have
                # their own short timeouts to also fail). 3s was overkill.
                timeout=1,
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


def _running_local_jobs() -> set[str]:
    """Return the names from PROCESS_PATTERNS whose pattern matches a live
    process command line. One ps call, regex in Python.

    Why not `pgrep -f`: when this orchestrator runs as a Python.framework
    binary on macOS, `pgrep -f` invoked as its child silently returns no
    matches for cmdlines in the parent process tree, even when ps shows
    them. Reading `ps -axww -o command=` and matching in Python sidesteps
    that and also drops three subprocess invocations down to one.
    """
    if shutil.which("ps") is None:
        return set()
    try:
        result = subprocess.run(
            ["ps", "-axww", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (subprocess.TimeoutExpired, OSError):
        return set()
    if result.returncode != 0 or not result.stdout:
        return set()
    matched: set[str] = set()
    for line in result.stdout.splitlines():
        cmdline = line.strip().partition(" ")[2]
        if not cmdline:
            continue
        for name, rx in PROCESS_PATTERNS.items():
            if name not in matched and rx.search(cmdline):
                matched.add(name)
        if len(matched) == len(PROCESS_PATTERNS):
            break
    return matched
