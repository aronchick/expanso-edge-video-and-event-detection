"""Tests for orchestrator/jetson_wan.py.

The controller has three branches we care about:
  1. fake mode (empty JETSON_HOST) — never calls subprocess, always reports
     ok+cosmetic_only. This is the laptop-dev path.
  2. ssh succeeds (returncode 0) — ok=True, cosmetic_only=False.
  3. ssh fails (nonzero returncode) — ok=False, cosmetic_only=True. The flag
     in the orchestrator still flips so the dashboard responds.

We monkeypatch subprocess.run rather than actually shelling out — running ssh
in CI would be flaky and irrelevant.
"""

from __future__ import annotations

import subprocess

import pytest

from expanso_security_camera.orchestrator.jetson_wan import (
    JetsonWanController,
    WanResult,
)


@pytest.mark.asyncio
async def test_fake_mode_when_host_unset(monkeypatch):
    monkeypatch.delenv("ARMYX_JETSON_HOST", raising=False)
    c = JetsonWanController(jetson_host="")
    result = await c.set_wan(up=False)
    assert result.ok is True
    assert result.cosmetic_only is True
    assert "ARMYX_JETSON_HOST not set" in result.detail


@pytest.mark.asyncio
async def test_ssh_success(monkeypatch):
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    c = JetsonWanController(jetson_host="nvidia@jetson")
    result = await c.set_wan(up=True)

    assert result.ok is True
    assert result.cosmetic_only is False
    # ssh argv contains the host and the remote nmcli command
    assert "nvidia@jetson" in captured["argv"]
    assert any("nmcli radio wifi on" in a for a in captured["argv"])


@pytest.mark.asyncio
async def test_ssh_failure_falls_back_to_cosmetic(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=255, stdout=b"", stderr=b"Permission denied"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    c = JetsonWanController(jetson_host="nvidia@jetson")
    result = await c.set_wan(up=False)

    assert result.ok is False
    assert result.cosmetic_only is True
    assert "Permission denied" in result.detail
    assert "ssh exit 255" in result.detail


@pytest.mark.asyncio
async def test_ssh_timeout_handled(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=4.0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    c = JetsonWanController(jetson_host="nvidia@jetson", ssh_timeout_sec=4.0)
    result = await c.set_wan(up=False)

    assert result.ok is False
    assert result.cosmetic_only is True
    assert "timed out" in result.detail


@pytest.mark.asyncio
async def test_ssh_not_on_path(monkeypatch):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(subprocess, "run", fake_run)
    c = JetsonWanController(jetson_host="nvidia@jetson")
    result = await c.set_wan(up=True)

    assert result.ok is False
    assert "ssh not on PATH" in result.detail


@pytest.mark.asyncio
async def test_remote_command_uses_argv_no_shell_injection(monkeypatch):
    """The whole point of subprocess.run with a list is no shell expansion.

    If someone slipped `; rm -rf /` into the radio_iface, shlex.quote should
    keep it inert AND the argv form means there's no shell to interpret it.
    """
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["shell"] = kwargs.get("shell", False)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    c = JetsonWanController(jetson_host="nvidia@jetson", radio_iface="wifi; rm -rf /")
    await c.set_wan(up=True)

    assert captured["shell"] is False
    # The remote command string is one argv element; the nasty payload is
    # quoted by shlex.quote, so even if SSH passed it through a remote shell,
    # it'd be a literal string arg to nmcli, not an injection.
    remote = next(a for a in captured["argv"] if "nmcli" in a)
    assert "'wifi; rm -rf /'" in remote


def test_wan_result_fields():
    # Lock in the public shape — api.py serializes these into WS frames.
    r = WanResult(ok=True, cosmetic_only=False, detail="ok")
    assert r.ok is True
    assert r.cosmetic_only is False
    assert r.detail == "ok"
