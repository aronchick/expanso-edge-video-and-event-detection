## Why

The hackathon demo currently lands as an edge-EO/IR sensor story
(YOLO + Gemini cascade on cameras). DiSCO™ — L3Harris's Distributed
Spectrum Collaboration and Operations architecture — is the EMSO
analogue of exactly this pattern: leading-edge sensors, AI/ML at
the node, cloud reachback, store-and-forward through DDIL, and a
pipeline-as-config control plane. To judges in the EW community, the
imagery framing reads as adjacent-but-not-mine.

This change adds a **DiSCO mode** to the existing dashboard: a
purely-cosmetic, judge-toggleable reskin that re-labels the imagery
demo as an RF/EMSO demo without any backend changes. It's the
"sensor changes, pipeline doesn't" punchline made visible — same
events.ndjson, same Expanso pipeline, same S3 archive, same DDIL
beats — just rendered with EW vocabulary and a fake spectrum
waterfall instead of camera tiles.

Hackathon-only scope: no real SDR, no real IQ data, no real threat
library. Three hours of dashboard work that doubles the demo's
audience reach.

## What Changes

- Add a **DiSCO mode toggle** to the dashboard (URL param `?mode=disco`
  and an `F5` operator keystroke). Toggle is client-side only; the
  orchestrator and pipeline don't know it exists.
- Add a **fake spectrum waterfall renderer** (HTML5 canvas) that
  replaces each sector camera tile when in DiSCO mode: scrolling
  noise floor with bright bursts painted on event arrival.
- Add a **DiSCO label map** that translates UI strings on the fly:
  `Sector north` → `C-Band Sensor`, `events/min` → `SOI/min`,
  `YOLO: person 78%` → `Classifier: TYPE-A SOI 78%`,
  `Trigger classes` → `SOI library v1`, etc. Map is one constants
  file the operator can edit pre-show without touching demo logic.
- Update `STAGE_RUNBOOK.md` and `HACKATHON_SCRIPT.md` with one
  paragraph each on when to flip into DiSCO mode and what to say.

NON-goals (explicitly out of scope for hackathon):
- Real SDR ingest (HackRF, USRP, vendor SDK)
- Real IQ-clip archive pipeline
- Cross-band correlation logic changes
- Threat-library cloud-authority enforcement
- Direction-finding, modulation matching, emitter geolocation

## Capabilities

### New Capabilities
- `disco-mode-dashboard`: toggleable cosmetic EMSO reskin of the
  existing edge-ISR dashboard. Defines the toggle mechanism, the
  label map, the spectrum waterfall renderer, and the operator
  controls. No backend behavior changes.

### Modified Capabilities
<!-- None. The existing dashboard, orchestrator, sensor, and archive
     pipeline have no openspec specs yet (openspec was just initialized
     for this change), and DiSCO mode adds a layer ON TOP without
     changing any of their requirements. -->

## Impact

- **Code**: `public/edge/index.html`, `public/edge/styles.css`,
  `public/edge/ws_client.js` (toggle wiring + waterfall canvas +
  label substitution); new `public/edge/disco-labels.js` (the
  translation map, kept separate so the operator can edit one file);
  no Python changes.
- **Docs**: `STAGE_RUNBOOK.md` (one row in keystroke table for F5),
  `HACKATHON_SCRIPT.md` (one paragraph on the EMSO framing — when to
  flip mode mid-demo if the audience is EW-leaning).
- **Tests**: extend `tests/test_armyx_smoke.py` with a check that
  `?mode=disco` URL param is honored and that `disco-labels.js`
  exists with the expected key set.
- **No** AWS, Expanso, Jetson, or sensor-side changes.
- **No** breaking changes — default load is the existing imagery
  dashboard; DiSCO mode is opt-in.
- **Operator workflow**: Beat 5 stays identical visually, just with
  RF labels if DiSCO mode is active. Cloud Egress tile narration
  changes from "events shipped to S3" to "SOI archive shipped to S3."
