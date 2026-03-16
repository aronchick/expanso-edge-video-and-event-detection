"""Tests for config.py — YAML loading, env var expansion, defaults."""

from __future__ import annotations

import yaml

from expanso_security_camera.config import (
    CameraConfig,
    DemoConfig,
    _expand_env_vars,
    _expand_recursive,
)


class TestExpandEnvVars:
    def test_no_vars(self):
        assert _expand_env_vars("hello world") == "hello world"

    def test_simple_var(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "replaced")
        assert _expand_env_vars("${TEST_VAR}") == "replaced"

    def test_var_with_default(self):
        assert _expand_env_vars("${NONEXISTENT:fallback}") == "fallback"

    def test_var_without_default_unchanged(self):
        result = _expand_env_vars("${DEFINITELY_NOT_SET}")
        assert result == "${DEFINITELY_NOT_SET}"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert _expand_env_vars("${A}-${B}") == "1-2"

    def test_empty_default(self):
        assert _expand_env_vars("${NOPE:}") == ""

    def test_non_string_passthrough(self):
        assert _expand_env_vars(42) == 42

    def test_rtsp_url_expansion(self, monkeypatch):
        monkeypatch.setenv("CAM_USER", "admin")
        monkeypatch.setenv("CAM_PASS", "secret")
        monkeypatch.setenv("CAM_IP", "192.168.1.10")
        result = _expand_env_vars("rtsp://${CAM_USER}:${CAM_PASS}@${CAM_IP}:554/stream")
        assert result == "rtsp://admin:secret@192.168.1.10:554/stream"


class TestExpandRecursive:
    def test_dict(self, monkeypatch):
        monkeypatch.setenv("X", "val")
        result = _expand_recursive({"key": "${X}"})
        assert result == {"key": "val"}

    def test_list(self, monkeypatch):
        monkeypatch.setenv("X", "val")
        result = _expand_recursive(["${X}", "plain"])
        assert result == ["val", "plain"]

    def test_nested(self, monkeypatch):
        monkeypatch.setenv("X", "val")
        result = _expand_recursive({"a": {"b": "${X}"}})
        assert result == {"a": {"b": "val"}}

    def test_non_string_types_untouched(self):
        assert _expand_recursive(42) == 42
        assert _expand_recursive(3.14) == 3.14
        assert _expand_recursive(True) is True


class TestCameraConfig:
    def test_defaults(self):
        cam = CameraConfig(camera_id="test", url="rtsp://test")
        assert cam.counting_line_y == 350
        assert cam.count_direction == "down"
        assert cam.direction_filter == "right"
        assert cam.role == "outside"

    def test_all_fields(self):
        cam = CameraConfig(
            camera_id="cam-1",
            url="rtsp://x",
            counting_line_y=200,
            counting_line_x_start=10,
            counting_line_x_end=500,
            count_direction="up",
            direction_filter="left",
            role="inside",
        )
        assert cam.camera_id == "cam-1"
        assert cam.role == "inside"


class TestDemoConfig:
    def test_from_yaml(self, sample_config_yaml):
        config = DemoConfig.from_yaml(sample_config_yaml)
        assert config.device_id == "test-device"
        assert config.site_id == "test-site"
        assert config.detect_mode == "box"
        assert len(config.cameras) == 2
        assert config.cameras[0].camera_id == "cam-outside"
        assert config.cameras[1].role == "inside"
        assert config.confidence_threshold == 0.3
        assert config.class_map == {"suitcase": "box", "backpack": "box"}

    def test_from_yaml_with_env_vars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_DEVICE", "jetson-42")
        config_data = {"device_id": "${MY_DEVICE}", "cameras": []}
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml.dump(config_data))
        config = DemoConfig.from_yaml(str(config_path))
        assert config.device_id == "jetson-42"

    def test_from_yaml_loads_dotenv(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_SITE=warehouse-1\n# comment\n\n")
        config_data = {"site_id": "${MY_SITE}", "cameras": []}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data))
        config = DemoConfig.from_yaml(str(config_path))
        assert config.site_id == "warehouse-1"

    def test_default_dock_door(self):
        config = DemoConfig.default_dock_door()
        assert config.detect_mode == "box"
        assert len(config.cameras) == 2
        assert config.cameras[0].role == "outside"
        assert config.cameras[1].role == "inside"
        assert "suitcase" in config.class_map

    def test_default_people_counting(self):
        config = DemoConfig.default_people_counting()
        assert config.detect_mode == "person"
        assert config.detect_classes == [0]

    def test_defaults(self):
        config = DemoConfig()
        assert config.model_name == "yolov8s"
        assert config.confidence_threshold == 0.3
        assert config.cooldown_seconds == 3.0
        assert config.size_filter_min_area == 3000
        assert config.inference_interval == 1

    def test_empty_cameras(self, tmp_path):
        config_path = tmp_path / "empty.yaml"
        config_path.write_text(yaml.dump({"cameras": []}))
        config = DemoConfig.from_yaml(str(config_path))
        assert config.cameras == []
