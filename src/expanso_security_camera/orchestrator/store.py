"""SQLite event store for the orchestrator.

Per HACKATHON_SCRIPT.md §9.1. Persists all events received from sensors so
the dashboard can backfill on reconnect and the correlator can look at a
recent window across sectors.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class EventStore:
    def __init__(
        self,
        db_path: str = "orchestrator.db",
        ndjson_path: str | Path | None = "events.ndjson",
    ) -> None:
        # NDJSON tail file — the event-archive Bloblang pipeline reads this.
        # Set to None to disable (e.g. tests).
        self.ndjson_path = Path(ndjson_path) if ndjson_path else None
        if self.ndjson_path:
            self.ndjson_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node TEXT NOT NULL,
                ts REAL NOT NULL,
                payload TEXT NOT NULL,
                queued_offline INTEGER DEFAULT 0
            )
            """
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_node_ts ON events(node, ts)")
        self.db.commit()
        self._lock = threading.Lock()

    def insert(self, event: dict) -> int:
        payload = json.dumps(event)
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO events (node, ts, payload, queued_offline) VALUES (?, ?, ?, ?)",
                (
                    event["node"],
                    event["ts"],
                    payload,
                    int(event.get("queued_offline", False)),
                ),
            )
            self.db.commit()
        # Append to the NDJSON tail file for the Bloblang archive pipeline.
        # Skip fused alerts (node=='fusion-node') so the archive only holds
        # sensor-emitted events; the pipeline filters anyway but this halves IO.
        if self.ndjson_path and event.get("node") != "fusion-node":
            try:
                with self.ndjson_path.open("a", encoding="utf-8") as f:
                    f.write(payload + "\n")
            except OSError:
                pass  # never let archive IO take down the fusion node
        return cur.lastrowid

    def recent(self, since_ts: float, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT payload FROM events WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                (since_ts, limit),
            ).fetchall()
        return [json.loads(p) for (p,) in rows]
