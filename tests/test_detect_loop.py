"""scripts/detect_loop.py: environment handling, camera sources, model choice."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "detect_loop.py"
spec = importlib.util.spec_from_file_location("detect_loop", SCRIPT)
detect_loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detect_loop)


def test_parse_env_file_skips_comments_and_keeps_equals_in_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CAM_USER=admin\n# comment\n\nURL=rtsp://user:p=ss@host\n")
    env = detect_loop.parse_env_file(env_file)

    assert env == {"CAM_USER": "admin", "URL": "rtsp://user:p=ss@host"}
    assert detect_loop.parse_env_file(tmp_path / "missing") == {}


def test_camera_sources_prefer_a_full_url_over_credentials():
    env = {
        "CAM_USER": "admin",
        "CAM_PASS_OUTSIDE": "p1",
        "CAM_IP_OUTSIDE": "192.168.1.10",
        "CAM_URL_INSIDE": "/data/recordings/inside.mp4",
    }
    sources = detect_loop.camera_sources(env)

    assert sources["cam-outside"] == "rtsp://admin:p1@192.168.1.10:554/h264Preview_01_sub"
    assert sources["cam-inside"] == "/data/recordings/inside.mp4"


def test_choose_model_prefers_the_finetuned_weights(tmp_path):
    assert detect_loop.choose_model(tmp_path, None) == ("yolov8s-worldv2.pt", 0.08, True)
    (tmp_path / detect_loop.FINETUNED_NAME).touch()
    weights, conf, world = detect_loop.choose_model(tmp_path, None)

    assert weights.endswith(detect_loop.FINETUNED_NAME)
    assert (conf, world) == (0.25, False)
    assert detect_loop.choose_model(tmp_path, "custom.pt")[0] == "custom.pt"


def test_scan_event_matches_the_pipeline_contract():
    event = detect_loop.scan_event({"cam-outside": {"boxes": 3, "status": "online"}})

    assert event["schema_version"] == "1.0.0"
    assert event["event_type"] == "detection_scan"
    assert json.loads(json.dumps(event))["detections"]["cam-outside"]["boxes"] == 3
