"""Event emission with offline tolerance.

Per HACKATHON_SCRIPT.md §8.3. Always writes locally first. Tries to push
upstream to the orchestrator. If the orchestrator is unreachable, events
queue locally in SQLite and replay on reconnect, marked queued_offline=true
so the dashboard can highlight them and the correlator can ignore them.

Uses urllib (stdlib) instead of requests to match the existing dataset.py
pattern and avoid pulling in another dependency.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request

from expanso_security_camera.sensor.schema import Event


class Emitter:
    def __init__(self, node_id: str, db_path: str, orchestrator_url: str) -> None:
        self.node_id = node_id
        self.orchestrator_url = orchestrator_url.rstrip("/")
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                payload TEXT NOT NULL,
                pushed INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_pushed ON events(pushed)")
        self.db.commit()
        self._lock = threading.Lock()

        self._stop = False
        self._drain_thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._drain_thread.start()

    def emit(self, event: Event) -> None:
        payload_dict = event.model_dump()
        payload = json.dumps(payload_dict, default=str)
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO events (ts, payload, pushed) VALUES (?, ?, 0)",
                (event.ts, payload),
            )
            row_id = cur.lastrowid
            self.db.commit()

        if self._push(payload):
            with self._lock:
                self.db.execute("UPDATE events SET pushed=1 WHERE id=?", (row_id,))
                self.db.commit()

    def _push(self, payload: str) -> bool:
        try:
            req = urllib.request.Request(
                f"{self.orchestrator_url}/events",
                data=payload.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            return False

    def _drain_loop(self) -> None:
        while not self._stop:
            time.sleep(5)
            with self._lock:
                rows = self.db.execute(
                    "SELECT id, payload FROM events WHERE pushed=0 ORDER BY ts LIMIT 50"
                ).fetchall()
            for row_id, payload in rows:
                p = json.loads(payload)
                p["queued_offline"] = True
                if self._push(json.dumps(p)):
                    with self._lock:
                        self.db.execute("UPDATE events SET pushed=1 WHERE id=?", (row_id,))
                        self.db.commit()
                else:
                    break  # still offline; try next tick

    def close(self) -> None:
        self._stop = True
