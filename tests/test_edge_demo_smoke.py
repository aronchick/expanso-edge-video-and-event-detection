"""Smoke tests for the public Edge ISR demo surface."""

from __future__ import annotations

import subprocess
from pathlib import Path

from expanso_security_camera.orchestrator.snapshots import synthesize_awaiting_start

ROOT = Path(__file__).resolve().parent.parent


def test_setup_script_parses():
    result = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "setup_jetson_lan.sh")],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_dashboard_has_gateway_and_egress_contracts():
    html = (ROOT / "public" / "edge" / "index.html").read_text()
    for marker in (
        "metric-gemini",
        "Analyst replay",
        "MODEL GATEWAY · ANALYST",
        "egress-tile",
        "s3-modal",
    ):
        assert marker in html


def test_dashboard_js_handles_model_answers_and_s3():
    js = (ROOT / "public" / "edge" / "ws_client.js").read_text()
    for marker in (
        "Gemini Augmented",
        "gemini_description",
        "metric-gemini",
        "renderS3",
        "openS3Modal",
    ):
        assert marker in js


def test_awaiting_start_snapshot_is_a_real_jpeg():
    for sector in ("sensor-north", "sensor-south"):
        image = synthesize_awaiting_start(sector)
        assert image.startswith(b"\xff\xd8")
        assert len(image) > 5000
