"""Tests for the cross-zone people tally + crowd threshold (zones.py).

This is the headline of the people-counting demo: the merged reading across
both cameras flags, with a SHAPE requirement — the crowd must span both
cameras. These tests lock in:
  * per-zone counts come from the person hits in the latest event
  * the combined total sums the zones
  * the FLAG rule: combined >= min_total AND every zone >= 1 AND one zone >= 2
    (default "at least three across both, one in each, two in one")
  * a cluster in a single zone does NOT flag — the merge must be spread
  * the alert is edge-triggered (fires once per crossing, cooldown-guarded)
  * stale zones decay to 0 instead of pinning an old crowd reading
  * offline/replayed events and non-person classes don't inflate the count
"""

from __future__ import annotations

from expanso_security_camera.orchestrator.zones import ZoneCounter


def _evt(node: str, n_person: int, *, offline: bool = False, extra: list | None = None) -> dict:
    hits = [{"label": "person", "confidence": 0.8} for _ in range(n_person)]
    if extra:
        hits.extend(extra)
    return {"node": node, "ts": 0.0, "yolo_hits": hits, "queued_offline": offline}


def test_per_zone_count_from_person_hits():
    zc = ZoneCounter()
    zc.record(_evt("sensor-north", 3), now=100.0)
    zc.record(_evt("sensor-south", 2), now=100.0)
    snap = zc.snapshot(now=100.0)
    assert snap["counts"] == {"sensor-north": 3, "sensor-south": 2}
    assert snap["total"] == 5


def test_combined_total_over_threshold():
    zc = ZoneCounter(threshold=5)
    zc.record(_evt("sensor-north", 3), now=10.0)
    zc.record(_evt("sensor-south", 3), now=10.0)
    snap = zc.snapshot(now=10.0)
    assert snap["total"] == 6
    assert snap["over"] is True


def test_at_min_total_is_over():
    # "at least N" is inclusive: exactly the min total, spread across both
    # zones (3+2=5 with threshold=5), DOES flag.
    zc = ZoneCounter(threshold=5)
    zc.record(_evt("sensor-north", 3), now=1.0)
    zc.record(_evt("sensor-south", 2), now=1.0)
    snap = zc.snapshot(now=1.0)
    assert snap["total"] == 5
    assert snap["over"] is True


def test_below_min_total_is_not_over():
    # One under the floor (2+2=4 with threshold=5) → no flag.
    zc = ZoneCounter(threshold=5)
    zc.record(_evt("sensor-north", 2), now=1.0)
    zc.record(_evt("sensor-south", 2), now=1.0)
    snap = zc.snapshot(now=1.0)
    assert snap["total"] == 4
    assert snap["over"] is False


def test_single_zone_alone_does_not_flag():
    # 4 in one zone, 0 in the other → above the total floor, but NOT spread
    # across both cameras → no flag. The merge is the point.
    zc = ZoneCounter(threshold=3)
    zc.record(_evt("sensor-north", 4), now=1.0)
    zc.record(_evt("sensor-south", 0), now=1.0)
    assert zc.snapshot(now=1.0)["over"] is False


# ── The headline rule, with default ZoneCounter() (min_total=3, ≥1 each, ≥2 in
#    one): "at least three across the two cameras, at least 1 in each, two or
#    more in one." ──────────────────────────────────────────────────────────
def test_default_rule_flags_two_plus_one():
    zc = ZoneCounter()
    zc.record(_evt("sensor-north", 2), now=1.0)
    zc.record(_evt("sensor-south", 1), now=1.0)
    snap = zc.snapshot(now=1.0)
    assert snap["total"] == 3
    assert snap["over"] is True


def test_default_rule_one_each_two_total_does_not_flag():
    zc = ZoneCounter()
    zc.record(_evt("sensor-north", 1), now=1.0)
    zc.record(_evt("sensor-south", 1), now=1.0)
    assert zc.snapshot(now=1.0)["over"] is False  # total 2 < 3


def test_default_rule_three_in_one_zone_does_not_flag():
    # 3 total but all in one camera (0 in the other) → not spread → no flag.
    zc = ZoneCounter()
    zc.record(_evt("sensor-north", 3), now=1.0)
    zc.record(_evt("sensor-south", 0), now=1.0)
    assert zc.snapshot(now=1.0)["over"] is False


def test_default_rule_two_in_each_flags():
    zc = ZoneCounter()
    zc.record(_evt("sensor-north", 2), now=1.0)
    zc.record(_evt("sensor-south", 2), now=1.0)
    assert zc.snapshot(now=1.0)["over"] is True


def test_non_person_classes_do_not_count():
    zc = ZoneCounter(threshold=5)
    extra = [{"label": "backpack", "confidence": 0.7}, {"label": "drone", "confidence": 0.9}]
    zc.record(_evt("sensor-north", 1, extra=extra), now=1.0)
    assert zc.snapshot(now=1.0)["counts"]["sensor-north"] == 1


def test_offline_events_ignored():
    zc = ZoneCounter(threshold=5)
    zc.record(_evt("sensor-north", 9, offline=True), now=1.0)
    assert zc.snapshot(now=1.0)["counts"]["sensor-north"] == 0


def test_fusion_node_events_ignored():
    zc = ZoneCounter(threshold=5)
    zc.record({"node": "fusion-node", "ts": 0.0, "yolo_hits": []}, now=1.0)
    assert "fusion-node" not in zc.snapshot(now=1.0)["counts"]


def test_stale_zone_decays_to_zero():
    zc = ZoneCounter(threshold=5, stale_sec=6.0)
    zc.record(_evt("sensor-north", 4), now=100.0)
    zc.record(_evt("sensor-south", 4), now=100.0)
    assert zc.snapshot(now=100.0)["over"] is True
    # 7 seconds later with no new events, both zones are stale → total 0.
    snap = zc.snapshot(now=107.0)
    assert snap["counts"] == {"sensor-north": 0, "sensor-south": 0}
    assert snap["over"] is False


def test_heartbeat_empty_event_resets_count():
    zc = ZoneCounter(threshold=5)
    zc.record(_evt("sensor-north", 3), now=1.0)
    assert zc.snapshot(now=1.0)["counts"]["sensor-north"] == 3
    # An empty heartbeat event (no person hits) zeroes the zone immediately.
    zc.record(_evt("sensor-north", 0), now=2.0)
    assert zc.snapshot(now=2.0)["counts"]["sensor-north"] == 0


def test_alert_edge_triggered_once_per_crossing():
    zc = ZoneCounter(threshold=5, cooldown_sec=8.0)
    # Below threshold → no alert.
    zc.record(_evt("sensor-north", 2), now=0.0)
    zc.record(_evt("sensor-south", 2), now=0.0)
    assert zc.evaluate_alert(now=0.0) is None

    # Cross above → fires exactly once.
    zc.record(_evt("sensor-north", 3), now=1.0)
    zc.record(_evt("sensor-south", 3), now=1.0)
    alert = zc.evaluate_alert(now=1.0)
    assert alert is not None
    assert alert["rule"] == "crowd_threshold"
    assert alert["total"] == 6
    assert alert["threshold"] == 5

    # Still over on the next beat → no re-fire (latched on the edge).
    zc.record(_evt("sensor-north", 3), now=2.0)
    zc.record(_evt("sensor-south", 3), now=2.0)
    assert zc.evaluate_alert(now=2.0) is None


def test_alert_refires_after_dropping_and_recrossing():
    zc = ZoneCounter(threshold=5, cooldown_sec=8.0)
    zc.record(_evt("sensor-north", 3), now=1.0)
    zc.record(_evt("sensor-south", 3), now=1.0)
    assert zc.evaluate_alert(now=1.0) is not None

    # Drop back under threshold — re-arms the edge.
    zc.record(_evt("sensor-north", 1), now=2.0)
    zc.record(_evt("sensor-south", 1), now=2.0)
    assert zc.evaluate_alert(now=2.0) is None

    # Cross again after the cooldown has elapsed → fires again.
    zc.record(_evt("sensor-north", 4), now=12.0)
    zc.record(_evt("sensor-south", 4), now=12.0)
    assert zc.evaluate_alert(now=12.0) is not None


def test_alert_contacts_carry_per_zone_counts():
    zc = ZoneCounter(threshold=5)
    zc.record(_evt("sensor-north", 4), now=1.0)
    zc.record(_evt("sensor-south", 3), now=1.0)
    alert = zc.evaluate_alert(now=1.0)
    assert alert is not None
    by_zone = {c["sector"]: c["count"] for c in alert["contacts"]}
    assert by_zone == {"sensor-north": 4, "sensor-south": 3}
