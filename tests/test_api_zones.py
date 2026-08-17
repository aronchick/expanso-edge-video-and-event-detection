"""End-to-end tests for the zone tally wired through the FastAPI app.

Proves the /events → /zones → crowd-alert path the dashboard depends on:
posting per-zone person events updates the combined tally, and the
combined total crossing the threshold fires exactly one fused alert.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from expanso_security_camera.orchestrator.api import create_app


def _app(tmp_path):
    return create_app(
        db_path=str(tmp_path / "o.db"),
        triggers_path=str(tmp_path / "t.yaml"),
        snapshots_dir=str(tmp_path / "snap"),
        ndjson_path=str(tmp_path / "e.ndjson"),
        fake_mode=True,
    )


def _person_event(node: str, n: int) -> dict:
    return {
        "node": node,
        "ts": 0.0,
        "yolo_hits": [
            {"label": "person", "confidence": 0.8, "bbox": [0, 0, 10, 10]} for _ in range(n)
        ],
        "signature": "sig",
    }


def test_zones_endpoint_starts_empty(tmp_path):
    client = TestClient(_app(tmp_path))
    z = client.get("/zones").json()
    assert z["total"] == 0
    assert z["over"] is False
    assert z["threshold"] == 3  # min combined total (the "/3" denominator)


def test_zones_endpoint_reflects_posted_counts(tmp_path):
    client = TestClient(_app(tmp_path))
    client.post("/events", json=_person_event("sensor-north", 3))
    client.post("/events", json=_person_event("sensor-south", 2))
    z = client.get("/zones").json()
    assert z["counts"]["sensor-north"] == 3
    assert z["counts"]["sensor-south"] == 2
    assert z["total"] == 5
    # 3 + 2 spans both cameras and clears the floor (>=3, >=1 each, >=2 in one).
    assert z["over"] is True


def test_combined_crossing_fires_one_crowd_alert(tmp_path):
    client = TestClient(_app(tmp_path))
    # 3 + 3 spans both cameras and clears the floor → one fused alert.
    client.post("/events", json=_person_event("sensor-north", 3))
    client.post("/events", json=_person_event("sensor-south", 3))

    z = client.get("/zones").json()
    assert z["total"] == 6
    assert z["over"] is True

    m = client.get("/metrics").json()
    assert m["fused_alerts"] == 1, "combined crossing must fire exactly one crowd alert"


def test_ws_connect_sends_zones(tmp_path):
    client = TestClient(_app(tmp_path))
    with client.websocket_connect("/ws") as ws:
        # Drain the on-connect burst; one of the messages must be the zones snapshot.
        saw_zones = False
        for _ in range(8):
            msg = ws.receive_json()
            if msg.get("type") == "zones":
                saw_zones = True
                assert "total" in msg["data"]
                break
        assert saw_zones, "dashboard relies on a zones snapshot on WS connect"
