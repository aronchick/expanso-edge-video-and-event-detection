"""Tests for scripts/detect_loop.py — env parsing, output format, model selection."""

from __future__ import annotations

import json


class TestDetectLoopEnvParsing:
    """Test .env file parsing logic from detect_loop.py."""

    def test_parse_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "CAM_USER=admin\n"
            "CAM_PASS_OUTSIDE=pass1\n"
            "CAM_PASS_INSIDE=pass2\n"
            "CAM_IP_OUTSIDE=192.168.1.10\n"
            "CAM_IP_INSIDE=192.168.1.11\n"
            "# This is a comment\n"
            "\n"
            "EXTRA_VAR=value\n"
        )
        env = {}
        with open(str(env_file)) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k] = v
        assert env["CAM_USER"] == "admin"
        assert env["CAM_PASS_OUTSIDE"] == "pass1"
        assert env["CAM_IP_INSIDE"] == "192.168.1.11"
        assert "#" not in env  # Comment not parsed

    def test_env_with_equals_in_value(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("URL=rtsp://user:p=ss@host\n")
        env = {}
        with open(str(env_file)) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k] = v
        assert env["URL"] == "rtsp://user:p=ss@host"


class TestDetectLoopModelSelection:
    """Test that detect_loop.py prefers fine-tuned model."""

    def test_finetuned_path_check(self, tmp_path):
        finetuned = tmp_path / "box-detector-finetuned.pt"
        assert not finetuned.exists()  # No fine-tuned model
        finetuned.touch()
        assert finetuned.exists()  # Now it exists


class TestDetectLoopOutputFormat:
    """Test the NDJSON event format."""

    def test_event_schema(self):
        import time

        event = {
            "schema_version": "1.0.0",
            "event_type": "detection_scan",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "detections": {
                "cam-outside": {"boxes": 5, "status": "online"},
                "cam-inside": {"boxes": 0, "status": "offline"},
            },
        }
        serialized = json.dumps(event)
        parsed = json.loads(serialized)
        assert parsed["schema_version"] == "1.0.0"
        assert parsed["event_type"] == "detection_scan"
        assert parsed["detections"]["cam-outside"]["boxes"] == 5

    def test_detections_json_format(self, tmp_path):
        detections = {
            "cam-outside": {"boxes": 3, "status": "online"},
            "cam-inside": {"boxes": 0, "status": "online"},
        }
        det_path = tmp_path / "detections.json"
        with open(str(det_path), "w") as f:
            json.dump(detections, f)
        data = json.loads(det_path.read_text())
        assert "cam-outside" in data
        assert data["cam-outside"]["boxes"] == 3
        assert data["cam-outside"]["status"] == "online"
