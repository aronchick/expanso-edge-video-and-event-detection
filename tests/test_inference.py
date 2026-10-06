"""Tests for inference.py — class mapping, command checking, camera thread."""

from __future__ import annotations

import json
from pathlib import Path

from expanso_security_camera.inference import check_commands, map_class_name


class TestMapClassName:
    def test_class_map_lookup(self):
        cmap = {"suitcase": "box", "backpack": "box"}
        assert map_class_name("suitcase", cmap, "box") == "box"
        assert map_class_name("backpack", cmap, "box") == "box"

    def test_yolo_world_box_classes(self):
        cmap = {}
        assert map_class_name("cardboard box", cmap, "box") == "box"
        assert map_class_name("shipping box", cmap, "box") == "box"
        assert map_class_name("package", cmap, "box") == "box"
        assert map_class_name("carton", cmap, "box") == "box"
        assert map_class_name("container", cmap, "box") == "box"

    def test_person_mode(self):
        cmap = {}
        assert map_class_name("person", cmap, "person") == "person"
        assert map_class_name("suitcase", cmap, "person") == "box"

    def test_unknown_class_defaults_to_box(self):
        assert map_class_name("unknown_thing", {}, "box") == "box"

    def test_class_map_takes_precedence(self):
        cmap = {"person": "human"}
        assert map_class_name("person", cmap, "box") == "human"


class TestCheckCommands:
    def test_reads_reset_command(self, tmp_path):
        cmd_path = str(tmp_path / "commands.json")
        Path(cmd_path).write_text(json.dumps({"action": "reset"}))
        result = check_commands(cmd_path)
        assert result == "reset"
        # File should be deleted after reading
        assert not Path(cmd_path).exists()

    def test_no_file_returns_none(self, tmp_path):
        result = check_commands(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_corrupt_json_returns_none(self, tmp_path):
        cmd_path = str(tmp_path / "commands.json")
        Path(cmd_path).write_text("not json")
        result = check_commands(cmd_path)
        assert result is None

    def test_missing_action_key(self, tmp_path):
        cmd_path = str(tmp_path / "commands.json")
        Path(cmd_path).write_text(json.dumps({"other": "value"}))
        result = check_commands(cmd_path)
        assert result is None


class TestRecordedFileReplay:
    """A recorded file is played once at its own speed, then the thread reports finished."""

    def test_file_source_finishes_after_one_pass(self, tmp_path, monkeypatch):
        import time

        import cv2
        import numpy as np

        from expanso_security_camera.config import CameraConfig
        from expanso_security_camera.inference import CameraThread

        clip = tmp_path / "clip.mp4"
        writer = cv2.VideoWriter(str(clip), cv2.VideoWriter.fourcc(*"mp4v"), 30.0, (64, 48))

        for _ in range(12):
            writer.write(np.zeros((48, 64, 3), dtype=np.uint8))

        writer.release()
        monkeypatch.setenv("ESC_REPLAY_SPEED", "20")
        thread = CameraThread(CameraConfig(camera_id="cam-test", url=str(clip)))
        thread.start()
        deadline = time.time() + 10

        while not thread.finished and time.time() < deadline:
            time.sleep(0.05)

        thread.stop()

        assert thread.finished
