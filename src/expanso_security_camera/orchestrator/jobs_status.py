"""Expanso job inventory surface.

Per DEMO_UI_SPEC.md §3.3. The dashboard's EXPANSO PLATFORM tile polls
/jobs to render four status dots. We try to shell out to `expanso-cli`
for real status; if it isn't on PATH or returns non-JSON, fall back to
a synthetic inventory listing the jobs we EXPECT to see — but with
`status="pending"` (gray dot), NEVER `"running"` (green dot).

This honesty matters for DEMO_SCRIPT.md Beat 0: judges literally watch
the operator deploy four jobs and the panel must transition 0/4 → 4/4
in real time. If the synthetic fallback lied "running" before the deploy
ran, Beat 0 would be a no-op visually. The fallback now reflects intent
(jobs we want running) without claiming a state we can't verify.
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


class JobsStatus:
    """Caches the latest expanso-cli job list so we don't shell out on every poll."""

    def __init__(self, refresh_sec: float = 2.0) -> None:
        self._refresh_sec = refresh_sec
        self._last_refresh = 0.0
        self._cached: list[dict] = self._synthetic_inventory("pending")

    def get(self) -> list[dict]:
        now = time.time()
        if now - self._last_refresh > self._refresh_sec:
            self._cached = self._fetch()
            self._last_refresh = now
        return self._cached

    def _fetch(self) -> list[dict]:
        # The CLI is `expanso-cli`; older installs may also have an `expanso`
        # alias, so fall through to it if expanso-cli isn't found.
        cli = (
            "expanso-cli"
            if shutil.which("expanso-cli")
            else ("expanso" if shutil.which("expanso") else None)
        )
        if cli is None:
            return self._synthetic_inventory("pending")
        try:
            result = subprocess.run(
                [cli, "job", "list", "--output", "json"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode != 0:
                return self._synthetic_inventory("pending")
            data = json.loads(result.stdout)
            return self._normalize_real(data)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            return self._synthetic_inventory("pending")

    def _normalize_real(self, raw) -> list[dict]:
        # Best-effort normalization: expanso CLI output schemas vary.
        # We only need {name, type, status, role} for the UI. Fill from
        # EXPECTED_JOBS by name; mark anything else as 'extra'.
        by_name: dict[str, dict] = {j["name"]: dict(j) for j in EXPECTED_JOBS}
        items = raw if isinstance(raw, list) else raw.get("jobs", [])
        for item in items:
            name = item.get("Name") or item.get("name") or ""
            status = (item.get("Status") or item.get("status") or "running").lower()
            if name in by_name:
                by_name[name]["status"] = status
            else:
                by_name[name] = {
                    "name": name,
                    "type": (item.get("Type") or item.get("type") or "").lower(),
                    "role": "extra",
                    "status": status,
                }
        # Default missing jobs to 'pending' so the panel still shows them.
        for name, job in by_name.items():
            job.setdefault("status", "pending")
        return list(by_name.values())

    def _synthetic_inventory(self, status: str) -> list[dict]:
        return [{**j, "status": status} for j in EXPECTED_JOBS]
