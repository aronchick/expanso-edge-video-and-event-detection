"""Lock in the people-counting dashboard markers (zone tally + crowd FLAG).

Mirrors the marker-style guards in test_armyx_smoke.py: these IDs/text are
wired across index.html ↔ ws_client.js ↔ styles.css, and if any one
disappears the tally renders blank silently. Pin them.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EDGE = REPO_ROOT / "public" / "edge"


def test_index_has_combined_and_zone_markers():
    html = (EDGE / "index.html").read_text()
    for marker in [
        "combined-card",
        "combined-total",
        "combined-threshold",
        "combined-bar",
        "combined-breakdown",
        "combined-state",
        "zone-count-sensor-north",
        "zone-count-sensor-south",
        "zone-status-sensor-north",
        "zone-status-sensor-south",
        # The live ticker ids renderEvent writes into must survive the
        # restructure (they moved inside the zone cards).
        "events-sensor-north",
        "events-sensor-south",
    ]:
        assert marker in html, f"dashboard missing #{marker}"


def test_ws_client_handles_zones_message():
    js = (EDGE / "ws_client.js").read_text()
    assert "renderZones" in js
    assert "case 'zones'" in js
    # Crowd FLAG is the headline alert rule.
    assert "crowd_threshold" in js
    # The combined total + per-zone counts must be written by the renderer.
    assert "combined-total" in js
    assert "zone-count-" in js


def test_ws_client_polls_zones_endpoint():
    js = (EDGE / "ws_client.js").read_text()
    assert "/zones" in js, "dashboard must poll /zones so counts stay live between heartbeats"


def test_styles_have_combined_over_state():
    css = (EDGE / "styles.css").read_text()
    # The over-threshold FLAG treatment is the visual payoff — pin it.
    assert ".combined-card.over" in css
    assert ".zone-card-count" in css
