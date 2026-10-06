"""The replay sensor, the cascade decisions it shares with the live detector,
and the stdout contract the Expanso sensor pipeline reads."""

from __future__ import annotations

import json

import pytest

from expanso_security_camera.sensor.cascade import select_hits, threshold_for
from expanso_security_camera.sensor.replay import load_scene, run_replay_node


class _Triggers:
    def __init__(self, labels):
        self._labels = set(labels)

    def contains(self, label):
        return label in self._labels


def _scene(tmp_path, frames):
    path = tmp_path / "scene.jsonl"
    rows = [{"scene": {"model": "test-model", "frames": len(frames)}}, *frames]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    return path


def _frame(offset, *detections):
    return {
        "offset_s": offset,
        "frame": {"index": int(offset * 2), "width": 640, "height": 480},
        "detections": [
            {"label": label, "confidence": conf, "bbox": [1, 2, 30, 40]}
            for label, conf in detections
        ],
    }


def test_select_hits_applies_triggers_floors_and_airplane_mapping():
    raw = [
        ("person", 0.9, (0, 0, 1, 1)),
        ("person", 0.2, (0, 0, 1, 1)),  # below the 0.40 person floor
        ("backpack", 0.9, (0, 0, 1, 1)),  # not armed
        ("airplane", 0.9, (0, 0, 1, 1)),  # drone proxy, armed below
    ]
    hits = select_hits("n", _Triggers({"person", "drone"}), raw)

    assert [(h.label, h.confidence) for h in hits] == [("person", 0.9), ("drone", 0.9)]
    assert threshold_for("person") == 0.40


def test_load_scene_requires_frames(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text(json.dumps({"scene": {}}) + "\n")

    with pytest.raises(ValueError):
        load_scene(path)


def test_replay_prints_one_signed_json_event_per_frame(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("MODEL_GATEWAY_URL", "http://127.0.0.1:1")
    scene = _scene(
        tmp_path,
        [_frame(0.0), _frame(0.5, ("person", 0.9), ("person", 0.8)), _frame(1.0, ("person", 0.1))],
    )

    run_replay_node(
        "sensor-test",
        str(scene),
        "http://127.0.0.1:1",  # unreachable: events queue in SQLite
        str(tmp_path / "s.db"),
        _Triggers({"person"}),
        speed=100.0,
    )
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert [len(e["yolo_hits"]) for e in lines] == [0, 2, 0]
    assert all(e["node"] == "sensor-test" for e in lines)
    assert all(e["signature"].startswith("dbom:sha256:") for e in lines)
    assert lines[1]["model_versions"]["yolo"] == "test-model"
