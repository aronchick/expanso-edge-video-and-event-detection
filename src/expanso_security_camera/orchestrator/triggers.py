"""Hot-reloadable trigger class configuration.

The soft-path live-update mechanism: sensors poll a YAML file on disk for
the set of YOLO classes that should fire events. Updating the file
immediately changes what the sensor surfaces — no restart, no redeploy.

The orchestrator owns the file and exposes GET/POST endpoints so the file
can be updated by `expanso job` redeploys, by the dashboard, or by curl
during a live demo.
"""

from __future__ import annotations

import threading
from pathlib import Path

import yaml

DEFAULT_TRIGGERS: list[str] = [
    # Default set for the demo's "before" state. Aerial contacts (airplane,
    # drone) are intentionally excluded so the live-update beat in
    # HACKATHON_SCRIPT.md §13 Beat 3 lands — the operator adds them mid-demo.
    "person",
    "backpack",
    "handbag",
    "suitcase",
    "knife",
    "scissors",
    "cell phone",
    "laptop",
    "car",
    "truck",
    "bus",
    "bicycle",
    "motorcycle",
]


class TriggerStore:
    """Reads/writes the trigger class list YAML, thread-safe."""

    def __init__(self, path: Path | str = Path("triggers.yaml")) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._write_unlocked(DEFAULT_TRIGGERS)

    def get(self) -> list[str]:
        with self._lock:
            try:
                data = yaml.safe_load(self.path.read_text()) or {}
                triggers = data.get("triggers", DEFAULT_TRIGGERS)
                if not isinstance(triggers, list):
                    return list(DEFAULT_TRIGGERS)
                return [str(t) for t in triggers]
            except (OSError, yaml.YAMLError):
                return list(DEFAULT_TRIGGERS)

    def set(self, triggers: list[str]) -> list[str]:
        cleaned = [str(t).strip() for t in triggers if str(t).strip()]
        with self._lock:
            self._write_unlocked(cleaned)
        return cleaned

    def _write_unlocked(self, triggers: list[str]) -> None:
        self.path.write_text(yaml.safe_dump({"triggers": triggers}, sort_keys=False))
