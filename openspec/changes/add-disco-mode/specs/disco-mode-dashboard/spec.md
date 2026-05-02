## ADDED Requirements

### Requirement: DiSCO mode is opt-in via URL param and operator keystroke

The dashboard SHALL render in the existing imagery (EO/IR) skin by
default. DiSCO mode SHALL activate ONLY when the URL contains
`?mode=disco` OR the operator presses `F5` while focus is on the
dashboard. Mode state SHALL persist across page reloads via
`localStorage` so accidental refresh during the demo does not drop
the operator out of the active mode.

#### Scenario: Default load shows imagery skin

- **WHEN** the dashboard is loaded with no `mode` URL param and no
  prior `localStorage` entry
- **THEN** the rendered UI shows the existing camera tiles, the
  trigger-class chips, and all original labels (`Sector north`,
  `events/min`, `YOLO`, `Trigger classes`)
- **AND** no spectrum waterfall is rendered
- **AND** the orchestrator and pipeline state are unchanged

#### Scenario: URL param activates DiSCO mode on load

- **WHEN** the dashboard is loaded with `?mode=disco`
- **THEN** the rendered UI shows the EMSO labels and the spectrum
  waterfall in place of the camera tiles
- **AND** `localStorage.getItem("dashboardMode")` returns `"disco"`

#### Scenario: F5 toggles between modes live

- **GIVEN** the dashboard is rendered in either mode
- **WHEN** the operator presses `F5` (with focus on the dashboard
  background, not an input element)
- **THEN** the mode flips (imagery → DiSCO or DiSCO → imagery) within
  one frame of paint
- **AND** the new mode persists to `localStorage`
- **AND** the URL is updated to reflect the new mode (so a subsequent
  reload preserves it even if `localStorage` is cleared)

### Requirement: Backend is unaware of DiSCO mode

DiSCO mode SHALL be a presentation-layer concern only. The
orchestrator's REST/WebSocket API, the events.ndjson contents, the
S3 archive pipeline, and every upstream sensor SHALL behave
identically regardless of dashboard mode.

#### Scenario: Same WebSocket payload renders both ways

- **GIVEN** an event with `yolo_hits: [{label: "drone", confidence: 0.78}]`
  arrives on the WebSocket
- **WHEN** rendered in imagery mode
- **THEN** the event card shows `YOLO: drone 78%`
- **WHEN** the same event is rendered in DiSCO mode
- **THEN** the event card shows `Classifier: <DiSCO label for "drone"> 78%`
- **AND** no second WebSocket message was sent or required

#### Scenario: Pipeline output is unchanged

- **GIVEN** the dashboard is in DiSCO mode
- **WHEN** an event flows through the orchestrator and into S3 via
  the `armyx-tech-event-archive` pipeline
- **THEN** the S3 object payload is byte-identical to what would
  have been produced in imagery mode
- **AND** the lineage block (`archive.archived_at`, `pipeline`,
  `pipeline_version`, `bucket`) is unchanged

### Requirement: DiSCO label map is one editable file

All DiSCO-mode UI string substitutions SHALL live in a single file
(`public/edge/disco-labels.js`) that exports a flat object mapping
imagery-mode strings to DiSCO-mode strings. Adding a new translation
SHALL NOT require changes to any other file.

#### Scenario: Operator edits one file pre-show

- **GIVEN** the operator wants to change `C-Band Sensor` to `S-Band
  Sensor` for tomorrow's demo
- **WHEN** they edit the corresponding key in
  `public/edge/disco-labels.js` and reload the dashboard
- **THEN** the new label appears in DiSCO mode
- **AND** no other file required modification

#### Scenario: Missing translation falls back to original

- **GIVEN** an event arrives with a `yolo_hits[0].label` value that
  is NOT in the DiSCO label map
- **WHEN** rendered in DiSCO mode
- **THEN** the original imagery-mode label is displayed unchanged
- **AND** a single console warning is logged (not per-event, to
  avoid console spam during the demo)

### Requirement: Spectrum waterfall renderer

In DiSCO mode, each sector camera tile SHALL be replaced by a
canvas-based fake spectrum waterfall: a horizontally-scrolling
visualization where the X axis is frequency, the Y axis is time
(newest at top), and pixel intensity represents simulated signal
strength. The waterfall SHALL paint a bright burst at a frequency
slot whenever a sensor event arrives for that sector.

#### Scenario: Waterfall continuously scrolls when no events arrive

- **WHEN** no events have arrived in the last 5 seconds
- **THEN** the waterfall continues to scroll vertically at a steady
  cadence (target 8-15 fps)
- **AND** the painted noise floor is visible at all times
- **AND** CPU usage of the waterfall renderer stays under 5% on
  the demo Mac (M-series)

#### Scenario: Event arrival paints a burst

- **GIVEN** the dashboard is in DiSCO mode
- **WHEN** an event arrives for `sensor-north`
- **THEN** within 100ms a bright burst is painted on the
  sensor-north waterfall at a deterministic frequency slot derived
  from the event's `yolo_hits[0].label` (so each label maps to a
  consistent visual position across the demo)
- **AND** the burst fades over the next ~3 seconds as the waterfall
  scrolls

#### Scenario: Waterfall unaffected by Cloud Egress state

- **GIVEN** the dashboard is in DiSCO mode AND Beat 5A has fired
  (Jetson WAN down, Cloud Egress stalled)
- **WHEN** events continue to arrive over the LAN WebSocket
- **THEN** the waterfall continues to paint bursts on those events
- **AND** the visual difference vs Beat 5C drain is still visible
  in the Cloud Egress tile (which is unchanged by DiSCO mode)

### Requirement: DiSCO mode does not regress existing demo beats

Every operator beat that works in imagery mode SHALL also work in
DiSCO mode without any change to keystrokes, timing, or behavior.

#### Scenario: F1/F2 still toggle Jetson WAN

- **GIVEN** the dashboard is in DiSCO mode
- **WHEN** the operator presses `F1`
- **THEN** the Jetson WAN-down SSH still fires (`sudo nmcli radio
  wifi off`) and the Cloud Egress tile flips to `stalled` exactly
  as in imagery mode

#### Scenario: F4 trigger-class update still pulses chips

- **GIVEN** the dashboard is in DiSCO mode AND the trigger bar
  rendered as `SOI library v1` with chips
- **WHEN** the operator presses `F4`
- **THEN** the new chips animate in with the same green pulse, just
  rendered with their DiSCO labels (e.g. `drone` → `<DiSCO label
  for "drone">`)

#### Scenario: Fused alert still takes over the screen

- **GIVEN** the dashboard is in DiSCO mode
- **WHEN** the correlator emits a fused event
- **THEN** the full-screen MULTI-SECTOR CORRELATION takeover renders
  with the DiSCO-mapped sector and contact labels, not the imagery
  ones

### Requirement: DiSCO mode is detectable in smoke tests

The dashboard's static assets SHALL contain a stable sentinel that
the smoke-test suite can use to assert DiSCO mode is wired (without
running a browser).

#### Scenario: Smoke test confirms files exist with required keys

- **WHEN** the smoke test inspects `public/edge/disco-labels.js`
- **THEN** the file exists and exports a JS object containing at
  least these keys: `Sector north`, `Sector south`, `events/min`,
  `Trigger classes`, `YOLO`, `Cloud Egress`, `MULTI-SECTOR CORRELATION`
- **AND** `public/edge/ws_client.js` references both the URL param
  `mode=disco` and the `F5` keystroke handler
- **AND** `public/edge/index.html` contains a canvas element with
  `id="disco-waterfall-north"` and `id="disco-waterfall-south"`
