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
        "Analyst replay",
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


def test_dashboard_retains_alert_tile_and_controls():
    html = (ROOT / "public" / "edge" / "index.html").read_text()
    for marker in (
        'id="alert-tile"',
        'id="alert-state"',
        'id="alert-detail"',
        'id="alert-meta"',
        'id="theme-toggle"',
        'id="rotate-toggle"',
        'id="keys-toggle"',
        'id="conn-banner"',
        'role="tablist"',
        'name="viewport"',
    ):
        assert marker in html, f"dashboard missing {marker}"


def test_alert_tile_labels_rules_and_latches_crowd_flag():
    js = (ROOT / "public" / "edge" / "ws_client.js").read_text()
    for marker in (
        "ALERT_RULE_LABELS",
        "backpack_detected",
        "drone_after_update",
        "ALERT_HOLD_MS",
        "crowdLatchUntil",
        "flagCrowd",
    ):
        assert marker in js
    # Only the crowd rule may flag the combined card.
    assert "alert.rule === 'crowd_threshold'" in js
    # The socket reconnects in place; it must never reload the page.
    assert "location.reload" not in js


def test_visible_copy_has_no_stale_or_banned_wording():
    edge = ROOT / "public" / "edge"
    html = (edge / "index.html").read_text()
    js = (edge / "ws_client.js").read_text()
    for banned in ("illustrative", "modeled", "simulated", "demo scale"):
        assert banned not in html.lower()
        assert banned not in js.lower()
    assert "Gemini Augmented" not in html + js
    assert "\u2014" not in html, "no em dashes in the page copy"


def test_theme_system_is_light_by_default_with_explicit_dark():
    edge = ROOT / "public" / "edge"
    css = (edge / "styles.css").read_text()
    theme = (edge / "theme.js").read_text()
    ui = (edge / "ui.js").read_text()
    assert ':root[data-theme="dark"]' in css
    assert "prefers-color-scheme" not in css
    assert "edge-isr-theme" in theme and "try" in theme
    assert "aria-pressed" in ui
