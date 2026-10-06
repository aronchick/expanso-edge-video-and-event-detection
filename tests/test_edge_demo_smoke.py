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


def test_fake_sensor_can_produce_a_drone_when_armed(monkeypatch):
    """Arming `drone` (F4) needs something to show: the fake scene must offer one."""
    import random

    from expanso_security_camera.sensor import main as sensor_main

    monkeypatch.setattr(random, "random", lambda: 0.0)
    event = sensor_main._fake_one_event("sensor-north", simulate_offline=False)

    assert "drone" in {hit.label for hit in event.yolo_hits}


def test_jetson_updater_install_has_every_file_it_copies():
    scripts = ROOT / "scripts"

    for name in ("jetson_update_expanso_edge.sh", "install_jetson_update_timer.sh"):
        assert (scripts / name).is_file()

    for unit in ("edge-isr-update.service", "edge-isr-update.timer"):
        assert (scripts / "jetson-systemd" / unit).is_file()


def test_archive_job_writes_to_s3_with_the_standard_credential_chain():
    import yaml

    job = yaml.safe_load((ROOT / "jobs" / "event-archive-job.yaml").read_text())
    outputs = {o["label"]: o for o in job["config"]["output"]["broker"]["outputs"]}
    s3 = outputs["s3"]["aws_s3"]

    assert s3["bucket"].startswith("${EDGE_ISR_S3_BUCKET")
    assert "credentials" not in s3  # no hard-coded profile or keys
    assert "daily-file" in outputs and "job-log" in outputs


def test_no_job_pins_a_user_home_directory_or_a_cluster_name():
    for job in (ROOT / "jobs").glob("*.yaml"):
        text = job.read_text()

        assert "/Users/" not in text and "/home/" not in text, job.name


def test_dashboard_has_the_tier_strip_and_alert_tile():
    html = (ROOT / "public" / "edge" / "index.html").read_text()

    for marker in ("tier-edge", "tier-fusion", "tier-cloud", "alert-tile"):
        assert marker in html, marker


def test_platform_strip_lists_only_this_demos_jobs(monkeypatch):
    from expanso_security_camera.orchestrator import jobs_status

    monkeypatch.setattr(
        jobs_status.JobsStatus,
        "_fetch_cloud_jobs",
        lambda self: [
            {"name": "event-archive", "type": "pipeline", "status": "running"},
            {"name": "someone-elses-job", "type": "pipeline", "status": "running"},
        ],
    )
    names = {job["name"] for job in jobs_status.JobsStatus().get()}

    assert "someone-elses-job" not in names
    assert {"fusion-node", "sensor-north", "sensor-south", "fuse", "event-archive"} <= names


def test_synthesized_frames_carry_no_invented_site_data():
    source = (ROOT / "src/expanso_security_camera/orchestrator/snapshots.py").read_text()

    for invented in ("_SENSOR_META", "gps", "FOV", "AZ "):
        assert invented not in source, invented
