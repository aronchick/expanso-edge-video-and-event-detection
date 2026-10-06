"""Small client for the demo kit's localhost-only model gateway."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

GATEWAY_URL = os.environ.get("MODEL_GATEWAY_URL", "http://127.0.0.1:18143")


def ask(prompt: str, *, system: str = "", fixture: str = "", timeout: float = 3.0) -> dict:
    """Ask once; the gateway owns replay, authentication, caps, and caching."""
    payload = json.dumps({"prompt": prompt, "system": system, "fixture": fixture}).encode()
    request = urllib.request.Request(
        f"{GATEWAY_URL}/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read() or b"{}")
        except ValueError:
            body = {}
        reason = body.get("reason", f"HTTP {error.code}")
        raise RuntimeError(f"model gateway refused the request: {reason}") from error
