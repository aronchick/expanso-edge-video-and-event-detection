"""Offline tests for the demo-kit model gateway client."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from expanso_security_camera.model_gateway import ask


@patch("urllib.request.urlopen")
def test_ask_returns_gateway_body(mock_urlopen):
    response = MagicMock()
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    response.read.return_value = json.dumps(
        {"status": "ok", "source": "fixture", "text": "Person detected."}
    ).encode()
    mock_urlopen.return_value = response

    result = ask("Detected object labels: person.", fixture="scene-person")

    assert result["source"] == "fixture"
    assert result["text"] == "Person detected."


@patch("urllib.request.urlopen")
def test_ask_surfaces_gateway_refusal(mock_urlopen):
    body = json.dumps({"reason": "kill switch is on"}).encode()
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "url", 403, "Forbidden", {}, MagicMock(read=lambda: body)
    )

    with pytest.raises(RuntimeError, match="kill switch is on"):
        ask("Detected object labels: drone.", fixture="scene-drone")
