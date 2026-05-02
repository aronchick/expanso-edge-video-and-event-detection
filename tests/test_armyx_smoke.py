"""Smoke tests for the armyx-tech bootstrap surface.

Not unit tests — these prove the artifacts exist and have the right shape
so a wrong file path or mangled YAML can't ship undetected:
  - Each shell script parses with `bash -n` and is executable.
  - Each shell script's --help exits 0 and prints something.
  - Bootstrap fails fast (nonzero) when JETSON_HOST is missing.
  - Teardown refuses to run without --yes.
  - The S3 archive pipeline yaml parses and references the expected env vars.
  - The dashboard HTML has the new egress-tile + s3-modal markers.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
JOBS = REPO_ROOT / "jobs"


SHELL_SCRIPTS = [
    SCRIPTS / "bootstrap_armyx_tech.sh",
    SCRIPTS / "teardown_armyx_tech.sh",
    SCRIPTS / "setup_jetson_lan.sh",
    SCRIPTS / "demo_deploy_all.sh",
    SCRIPTS / "demo_reset.sh",
]


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_script_exists_and_executable(script):
    assert script.exists(), f"missing: {script}"
    mode = script.stat().st_mode
    assert mode & stat.S_IXUSR, f"not executable: {script}"


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_script_parses(script):
    # bash -n : parse without executing. Catches typos that would only
    # surface when a colleague runs the script during demo prep.
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True)
    assert r.returncode == 0, f"bash -n failed for {script}: {r.stderr.decode()}"


def test_bootstrap_help_works():
    r = subprocess.run(
        ["bash", str(SCRIPTS / "bootstrap_armyx_tech.sh"), "--help"],
        capture_output=True,
        timeout=10,
    )
    assert r.returncode == 0
    out = r.stdout.decode()
    # Help text must mention the things a user grepping for usage cares about.
    assert "JETSON_HOST" in out
    assert "armyx-tech" in out


def test_bootstrap_requires_jetson_host():
    """No JETSON_HOST => exit 1 fast, before any AWS calls."""
    env = {k: v for k, v in os.environ.items() if k != "JETSON_HOST"}
    r = subprocess.run(
        ["bash", str(SCRIPTS / "bootstrap_armyx_tech.sh")],
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert r.returncode != 0
    msg = (r.stderr.decode() + r.stdout.decode()).lower()
    assert "jetson_host" in msg


def test_teardown_refuses_without_yes():
    r = subprocess.run(
        ["bash", str(SCRIPTS / "teardown_armyx_tech.sh")],
        capture_output=True,
        timeout=10,
    )
    assert r.returncode != 0
    out = (r.stdout + r.stderr).decode().lower()
    assert "--yes" in out


def test_teardown_help_works():
    r = subprocess.run(
        ["bash", str(SCRIPTS / "teardown_armyx_tech.sh"), "--help"],
        capture_output=True,
        timeout=10,
    )
    assert r.returncode == 0
    assert b"--yes" in r.stdout or b"--yes" in r.stderr


def test_setup_jetson_lan_refuses_non_root():
    """Run as the test user (not root) — script should exit 1 with a clear msg."""
    if os.geteuid() == 0:
        pytest.skip("running as root; can't test the non-root failure path")
    r = subprocess.run(
        ["bash", str(SCRIPTS / "setup_jetson_lan.sh")],
        capture_output=True,
        timeout=10,
    )
    assert r.returncode != 0
    out = (r.stdout + r.stderr).decode().lower()
    assert "root" in out or "sudo" in out


# ── YAML pipeline -------------------------------------------------------


def test_archive_pipeline_yaml_parses():
    p = JOBS / "armyx-tech-event-archive.yaml"
    assert p.exists()
    data = yaml.safe_load(p.read_text())
    assert data["name"] == "armyx-tech-event-archive"
    assert data["type"] == "pipeline"
    assert "config" in data


def test_archive_pipeline_has_s3_output():
    p = JOBS / "armyx-tech-event-archive.yaml"
    raw = p.read_text()
    # We don't traverse into Bloblang strings — just check the structural
    # output block is right and key invariants hold. The bucket name is
    # hardcoded because this Bloblang dialect doesn't support env(...) —
    # that's a load-bearing comment, not a smell.
    data = yaml.safe_load(raw)
    outputs = data["config"]["output"]["broker"]["outputs"]
    s3_output = next((o for o in outputs if "aws_s3" in o), None)
    assert s3_output is not None, "pipeline must have an aws_s3 output"
    aws_s3 = s3_output["aws_s3"]
    assert aws_s3["bucket"].startswith("armyx-tech-edge-events-"), (
        "bucket name must be hardcoded to armyx-tech-edge-events-<account>"
    )
    assert aws_s3["region"], "S3 region must be set"
    # Key path uses uuid for uniqueness (date partitioning was dropped because
    # this Bloblang lacks now().ts_format)
    assert "uuid_v4()" in aws_s3["path"], "S3 key path must use uuid_v4 for uniqueness"


# ── Dashboard HTML ------------------------------------------------------


def test_dashboard_html_has_egress_tile():
    html = (REPO_ROOT / "public" / "edge" / "index.html").read_text()
    # These IDs are wired in ws_client.js — if any one disappears, renderS3
    # crashes silently. Lock them in.
    for marker in [
        "egress-tile",
        "egress-state",
        "egress-count",
        "egress-bucket",
        "egress-last",
        "egress-recent",
        "s3-modal",
        "s3-modal-title",
        "s3-modal-body",
        "s3-modal-close",
    ]:
        assert marker in html, f"dashboard missing #{marker}"


def test_dashboard_html_has_tier_strip():
    """Beat-1 architectural claim: EDGE → FUSION → CLOUD strip.

    When CLOUD goes down (F1), only the cloud dot dims — the other two
    stay green. The CSS rule `body.cloud-down #tier-cloud` does the work,
    so the IDs must be present.
    """
    html = (REPO_ROOT / "public" / "edge" / "index.html").read_text()
    for marker in ["tier-edge", "tier-fusion", "tier-cloud"]:
        assert marker in html, f"dashboard missing #{marker}"
    # The labels themselves must be visible at stage scale.
    assert "EDGE" in html
    assert "FUSION" in html
    assert "CLOUD" in html


def test_dashboard_html_has_gemini_reachback_pill():
    html = (REPO_ROOT / "public" / "edge" / "index.html").read_text()
    assert "metric-gemini" in html, "missing #metric-gemini value element"
    assert "Gemini reachback" in html, "missing 'Gemini reachback' label"


def test_dashboard_html_has_sector_subtitles():
    """Both sectors get a 'Tier 1 Analyst · YOLO+Gemini cascade' subtitle."""
    html = (REPO_ROOT / "public" / "edge" / "index.html").read_text()
    # Once per sector — 2 occurrences total.
    assert html.count("Tier 1 Analyst") >= 2, "expected 'Tier 1 Analyst' subtitle on both sectors"
    assert "YOLO+Gemini cascade" in html


def test_demo_deploy_help_works():
    """Beat 0 wraps this — must exit 0 on --help so the operator can read it."""
    r = subprocess.run(
        ["bash", str(SCRIPTS / "demo_deploy_all.sh"), "--help"],
        capture_output=True,
        timeout=10,
    )
    assert r.returncode == 0
    out = r.stdout.decode()
    assert "demo_deploy_all" in out or "DEMO_SCRIPT" in out


def test_demo_reset_help_works():
    r = subprocess.run(
        ["bash", str(SCRIPTS / "demo_reset.sh"), "--help"],
        capture_output=True,
        timeout=10,
    )
    assert r.returncode == 0
    out = r.stdout.decode()
    assert "demo_reset" in out or "rehearsal" in out


def test_jobs_status_synthetic_fallback_is_pending_not_running():
    """Beat 0 demands honesty: when no expanso-cli is reachable, the panel
    must report jobs as 'pending' (gray dot), NEVER 'running' (green dot).
    A green-dot synthetic fallback would lie about the deploy state and
    nullify Beat 0 visually.
    """
    src = (REPO_ROOT / "src/expanso_security_camera/orchestrator/jobs_status.py").read_text()
    # The synthetic_inventory helper exists; every CALL must use "pending".
    import re

    calls = re.findall(r'_synthetic_inventory\("([a-z]+)"\)', src)
    assert calls, "jobs_status.py must invoke _synthetic_inventory somewhere"
    assert all(s == "pending" for s in calls), (
        f"all _synthetic_inventory(...) calls must use 'pending', got: {calls}"
    )


def test_dashboard_html_has_platform_tagline():
    html = (REPO_ROOT / "public" / "edge" / "index.html").read_text()
    # Platform tile carries the canonical "0 new hardware" tagline.
    assert "platform-tagline" in html
    assert html.count('class="platform-tagline"') == 1, (
        "platform tile should carry exactly one platform-tagline element"
    )
    assert "0 new hardware" in html


def test_dashboard_js_handles_s3_message():
    js = (REPO_ROOT / "public" / "edge" / "ws_client.js").read_text()
    assert "renderS3" in js
    assert "case 's3'" in js
    # Modal handlers
    assert "s3-modal-close" in js
    assert "openS3Modal" in js
    # Gemini reachback wiring — the counter increments per-call and the pill
    # element must be referenced explicitly.
    assert "metric-gemini" in js, "ws_client.js must reference #metric-gemini"
    assert "gemini_description" in js, (
        "ws_client.js must read e.gemini_description to count reachback calls"
    )
    # Tier-dim behavior is purely a CSS rule keyed off body.cloud-down, so the
    # JS must keep toggling that body class. (setCloudState already does this;
    # the assertion locks the contract in.)
    assert "cloud-down" in js, "ws_client.js must toggle body.cloud-down so #tier-cloud dims"
