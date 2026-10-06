"""Device provenance is stamped only from a file the operator supplies."""

from __future__ import annotations

from fastapi.testclient import TestClient

from expanso_security_camera.orchestrator.api import create_app, load_devices


def _client(tmp_path, devices=None):
    return TestClient(
        create_app(
            db_path=str(tmp_path / "o.db"),
            triggers_path=str(tmp_path / "t.yaml"),
            snapshots_dir=str(tmp_path / "snap"),
            ndjson_path=str(tmp_path / "e.ndjson"),
            devices_path=devices,
        )
    )


def _post(client):
    event = {"node": "sensor-north", "ts": 1.0, "yolo_hits": [], "signature": "s"}

    assert client.post("/events", json=event).status_code == 200


def test_nothing_is_stamped_without_a_devices_file(tmp_path):
    _post(_client(tmp_path))

    assert "device" not in (tmp_path / "e.ndjson").read_text()


def test_devices_file_is_stamped_on_matching_nodes(tmp_path):
    devices = tmp_path / "devices.yaml"
    devices.write_text("sensor-north:\n  room: Lobby\n  sensor_serial: ABC-1\n")
    _post(_client(tmp_path, str(devices)))
    stored = (tmp_path / "e.ndjson").read_text()

    assert '"room": "Lobby"' in stored


def test_load_devices_ignores_missing_files(tmp_path):
    assert load_devices(tmp_path / "nope.yaml") == {}
    assert load_devices(None) == {}
