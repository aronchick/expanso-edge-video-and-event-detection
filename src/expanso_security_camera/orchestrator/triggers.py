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
    # People-counting demo: person is the whole story at the start. The
    # cross-zone tally (zones.py) sums person counts across both cameras
    # and flags on the combined total. Backpack / drone are opt-in extras
    # the operator can arm mid-demo (ghost chip / F4) to show a live
    # pipeline update, but they are NOT in the default set so the booth
    # demo opens clean on "count the people."
    "person",
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
