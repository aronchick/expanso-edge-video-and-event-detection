"""DBOM event signing.

Per HACKATHON_SCRIPT.md §8.4. Each event gets a signature binding model
versions, timestamp, node, and payload. This stub uses SHA-256; swap with
ed25519 + a real Makoto signing library for production. Interface is
identical so the rest of the pipeline doesn't change.
"""

from __future__ import annotations

import hashlib
import json

from expanso_security_camera.sensor.schema import Event


def sign_event(event: Event, signing_key_path: str | None = None) -> Event:
    payload = json.dumps(
        event.model_dump(exclude={"signature"}),
        sort_keys=True,
        default=str,
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    event.signature = f"dbom:sha256:{digest[:16]}"
    return event
