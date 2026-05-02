## ADDED Requirements

### Requirement: Shape primitive vocabulary is closed and named

The dashboard SHALL use exactly the seven named shape primitives
defined below. New visual elements MUST map to one of these primitives;
introducing an eighth primitive MUST go through a spec amendment, not
a code-level decision.

The seven primitives:

1. **`display-rectangle`** — large data zones with sharp 90° corners
   or ≤2px radius. Used for sector tiles, the cloud-egress tile, and
   the platform tile. Border: 1px solid surface-light; background:
   surface; no shadow. This is the only "container" shape allowed for
   first-order regions.
2. **`bracket-label`** — replaces all pill-shaped chips. Square
   corners, no fill, 1px border, ASCII-style brackets in the label
   itself: `[ PERSON ]`, `[ BACKPACK ]`, `[ DRONE ]`. Mono uppercase.
   Used for trigger classes, classification tags, signature kind tags.
3. **`led-status`** — filled square glyph (~0.6em) followed by a single
   space and an uppercase mono label: `■ LIVE`, `■ STANDBY`, `■ DOWN`.
   Used for every status badge in the dashboard. Replaces all
   pill-shaped status badges. Color of the square uses the existing
   semantic palette (`#3ddc84` ok, `#ffa726` alert, `#ff4d4f` down,
   `#9aa3ad` standby).
4. **`metric-block`** — vertical pair: caption-mono UPPERCASE label
   on top, dominant tabular-nums value below. Label-to-value font-size
   ratio MUST be ≥3×. Used for every counter (events/min, fused, jobs
   ratio, total events, DBOM signed, RTT, bytes archived).
5. **`data-row`** — single-line monospace row for event lists and
   recent-keys lists. Pattern: leading mission-elapsed time or ISO
   8601 UTC timestamp, separator (` :: ` or ` · `), payload,
   separator, signature/metadata. No card border, no padding-heavy
   box; the row IS the unit. Replaces card-style event presentations.
6. **`divider-line`** — 1px vertical or horizontal hairline used to
   separate adjacent `metric-block`s and to bound banner regions.
   Replaces pill containers grouping unrelated metrics. Color: a dim
   surface-light token from the existing palette (no new color).
7. **`frame-corner`** — military-style L-shaped corner brackets (~12px
   per leg, 1-2px stroke) decorating the four corners of critical
   zones: sector tiles, cloud-egress tile, fused-alert takeover.
   Reinforces "this is an instrument, not a card."

#### Scenario: Trigger class chip uses bracket-label, not pill

- **WHEN** the trigger bar renders the class `person`
- **THEN** the rendered DOM uses the `bracket-label` primitive
- **AND** the visible text is `[ PERSON ]` (uppercase, with brackets)
- **AND** the element has square corners (border-radius ≤ 2px)
- **AND** no `border-radius: 999px` (pill) class is applied

#### Scenario: Status badge uses led-status, not pill

- **WHEN** a sector tile renders its current status as live
- **THEN** the badge uses the `led-status` primitive
- **AND** a filled square glyph in the OK semantic color is followed
  by the uppercase label `LIVE`
- **AND** the badge is NOT pill-shaped (no `border-radius: 999px`)

#### Scenario: Sector tile uses display-rectangle with frame-corner

- **WHEN** a sector tile renders
- **THEN** the tile container uses the `display-rectangle` primitive
  (border-radius ≤ 2px, 1px border)
- **AND** four `frame-corner` L-brackets decorate its corners

#### Scenario: Adjacent metrics are separated by divider-line, not grouped in a pill

- **WHEN** the header renders multiple counters side-by-side
- **THEN** each counter is its own `metric-block`
- **AND** adjacent blocks are separated by a `divider-line`
- **AND** there is no pill-shaped container wrapping the group

#### Scenario: Eighth primitive is rejected

- **GIVEN** a developer wants to add a new "card with shadow" element
- **WHEN** they attempt to introduce a new shape category not in this
  list
- **THEN** the change is rejected at review
- **AND** the developer is directed to either map to an existing
  primitive or open a spec amendment

### Requirement: Status indicators use the LED system uniformly

Every status indication in the dashboard SHALL use the `led-status`
primitive. The label SHALL be uppercase mono. Critical states (DOWN,
FAULT, STALLED) SHALL blink at 1Hz; steady states (LIVE, STANDBY, OK)
SHALL NOT animate. Color SHALL be drawn only from the existing
semantic palette in `DEMO_UI_SPEC.md` §1.3.

#### Scenario: Critical state blinks, steady state does not

- **GIVEN** the cloud link is DOWN
- **THEN** the cloud-link `led-status` blinks the filled square at
  1Hz with the label `DOWN` in red (`#ff4d4f`)
- **GIVEN** a sensor is LIVE
- **THEN** its `led-status` displays a steady (non-blinking) green
  square with the label `LIVE`

#### Scenario: All status badges in the viewport use the same vocabulary

- **WHEN** the dashboard is rendered in default state
- **THEN** every status indicator visible in the viewport (sectors,
  cloud link, jobs, archive) uses the `led-status` primitive
- **AND** none use pill, dot-only, or emoji-traffic-light forms

#### Scenario: Accessible without color alone

- **WHEN** the dashboard is viewed under a deuteranopia color filter
- **THEN** the status text label (e.g., `LIVE`, `DOWN`) remains the
  authoritative information channel
- **AND** the LED color is supplementary, not the only signal

### Requirement: Metric hierarchy uses label-on-top, value-dominant, ≥3× ratio

Every numerical metric in the dashboard SHALL use the `metric-block`
primitive. The label SHALL appear above the value. The label SHALL be
caption-mono, UPPERCASE, dim (text-secondary token), and letter-spaced
(0.05-0.1em). The value SHALL be display-sized, tabular-nums, in the
text-primary token. The value's font-size MUST be at least 3× the
label's font-size.

#### Scenario: events/min metric uses metric-block

- **WHEN** the header renders the `events/min` counter
- **THEN** the rendered DOM contains a `metric-block` primitive
- **AND** the label `EVENTS/MIN` is on top, mono, uppercase,
  letter-spaced, in text-secondary color
- **AND** the value (e.g., `119`) is below, in display size, with
  tabular-nums, in text-primary color
- **AND** the value font-size is at least 3× the label font-size

#### Scenario: Multiple metrics in a row are separated by divider-line

- **WHEN** the footer renders `events`, `DBOM signed`, `fused`,
  `jobs n/m` side-by-side
- **THEN** each is its own `metric-block`
- **AND** thin `divider-line` hairlines separate adjacent blocks
- **AND** no enclosing pill or rounded container wraps the group

### Requirement: Operational language is uppercase, compressed, non-marketing

All operational labels in the dashboard SHALL be in UPPERCASE.
Empty/error-state copy SHALL be compressed ops-language and SHALL NOT
include onboarding or apology phrasing. Marketing slogans SHALL NOT
appear inside the operational dashboard frame.

#### Scenario: Section headings are uppercase

- **WHEN** the dashboard renders the `Trigger classes` section heading
- **THEN** the visible text is `TRIGGER CLASSES`
- **AND** the same applies to `SECTOR NORTH`, `SECTOR SOUTH`,
  `RECENT`, `EXPANSO PLATFORM`, `CLOUD EGRESS`

#### Scenario: Empty/error state is compressed ops-language

- **GIVEN** the `ARMYX_S3_BUCKET` environment variable is unset
- **WHEN** the cloud-egress tile renders
- **THEN** its state copy reads `ARCHIVE: STANDBY · CONFIGURE BUCKET`
- **AND** does NOT read `add bucket via .env or expanso job`
- **AND** does NOT include any onboarding or apology phrasing

#### Scenario: Marketing slogans are excluded from the operational frame

- **WHEN** the dashboard renders any region inside the grid container
  or the top/bottom banners
- **THEN** the string `Powered by Expanso · workload moves to the data`
  does NOT appear
- **AND** no other product slogan or call-to-action text appears
  within the operational frame

#### Scenario: Event row uses the compressed pattern

- **WHEN** an event arrives with `yolo_hits=[{label:"person",
  confidence:0.78}]` and caption `"carrying load"`
- **THEN** the rendered `data-row` matches the pattern
  `T+MM:SS :: 1×PERSON 78% :: "carrying load" :: dbom:0xab12...`
- **AND** the DBOM hash is truncated to a short prefix (8-12 hex
  chars) followed by an ellipsis, not the full hash

### Requirement: Expanso brand appears as a discreet wordmark, not as marketing copy

The Expanso brand SHALL remain inside the operational frame as a
visual wordmark/logo, NOT as textual marketing copy. The wordmark
SHALL appear in the bottom banner RIGHT region.

The wordmark SHALL be:

- ≤24px tall (caption-sized; MUST NOT compete with operational data)
- monochrome (single color tone, not full-color brand palette)
- rendered at 60-70% opacity against the banner background

The wordmark SHALL NOT be accompanied by any tagline, slogan, or
call-to-action text inside the operational frame.

#### Scenario: Bottom banner shows wordmark, not slogan

- **WHEN** the dashboard renders in default state
- **THEN** the bottom banner RIGHT region contains an Expanso
  wordmark element (image or CSS-drawn text wordmark)
- **AND** the wordmark renders at ≤24px tall
- **AND** the wordmark uses a monochrome color at 60-70% opacity
- **AND** no slogan, tagline, or call-to-action text is adjacent to
  the wordmark

#### Scenario: Slogan is not present anywhere in the dashboard

- **WHEN** the dashboard's full DOM is inspected
- **THEN** the string `Powered by Expanso · workload moves to the data`
  is absent from every rendered region (header, grid, footer, banners,
  fused-alert overlay)

### Requirement: Timestamps use ISO 8601 UTC format uniformly

All operational wall-clock timestamps in the dashboard SHALL render
in ISO 8601 extended UTC form: `YYYY-MM-DDTHH:MM:SSZ`. Example:
`2026-05-02T10:52:13Z`. There SHALL be no Zulu/local toggle, no DTG
(`DDHHMMz MMM YY`) format, and no keybinding for switching formats.

ISO 8601 UTC SHALL appear in:

- The classification + mission banner (CENTER slot)
- Every event row's leading wall-clock timestamp
- The fused-alert takeover metadata
- The cloud-egress tile's last-upload timestamp
- Each sector tile's last-event timestamp
- Any other operational wall-clock timestamp surface

Relative-age renderings (e.g., `47s ago`, mission-elapsed
`T+00:14:32`) are NOT timestamps and SHALL remain in their existing
relative form. Mission-elapsed time leading event rows
(`T+MM:SS :: ...`) is also a relative age, not a timestamp.

#### Scenario: Banner timestamp renders in ISO 8601 UTC

- **GIVEN** the current UTC time is May 2 2026 10:52:13
- **WHEN** the dashboard renders the banner CENTER slot timestamp
- **THEN** the visible text is `2026-05-02T10:52:13Z`
- **AND** the timestamp ticks at least once per second

#### Scenario: Event row uses ISO 8601 UTC for wall-clock timestamps

- **WHEN** an event row renders a wall-clock timestamp
- **THEN** the format is `YYYY-MM-DDTHH:MM:SSZ`
- **AND** the value does NOT render as `10:52:13 AM`, `021052Z MAY 26`,
  or any other civilian or DTG form

#### Scenario: All timestamp surfaces share the same format

- **WHEN** the dashboard is rendered in default fake-mode
- **THEN** every wall-clock timestamp visible in the viewport (banner
  CENTER, event rows, fused-alert metadata, cloud-egress last-upload,
  per-sector last-event) uses the identical ISO 8601 extended UTC
  format
- **AND** no two timestamp surfaces use differing formats

#### Scenario: Relative ages are preserved as-is

- **WHEN** the cloud-egress tile renders its `last upload` age
- **THEN** an age such as `47s ago` continues to render in relative
  form
- **AND** is NOT rewritten as an ISO 8601 timestamp
- **AND** the leading mission-elapsed `T+MM:SS` on event rows
  similarly remains in relative form

### Requirement: Classification + mission banner is present at top and bottom

Two thin (~24-32px tall) full-width banners SHALL be pinned to the
viewport top and the viewport bottom, OUTSIDE the existing grid
defined in `DEMO_UI_SPEC.md` §2. Each banner SHALL contain three
slots:

- **LEFT**: classification level (e.g., `UNCLASSIFIED`)
- **CENTER**: mission codename + ISO 8601 UTC timestamp (e.g.,
  `EXERCISE: OVERWATCH-26 · 2026-05-02T10:52:13Z`)
- **RIGHT**: operator callsign (e.g., `OPS-1`); the bottom banner
  RIGHT region additionally hosts the Expanso wordmark

Styling: dark surface background, mono uppercase, classification text
in the existing amber accent (`#ffa726`), thin 1px top/bottom borders.

The demo default SHALL be:

- LEFT: `UNCLASSIFIED`
- CENTER: `EXERCISE: OVERWATCH-26 · <live ISO 8601 UTC>`
- RIGHT: `OPS-1` (overridable per the operator-callsign requirement
  below)

The dashboard SHALL NEVER display a classification level above
`UNCLASSIFIED`. The string `EXERCISE:` or `DEMO:` MUST appear in the
CENTER slot to make the non-real nature unambiguous.

#### Scenario: Top and bottom banners both render with default content

- **WHEN** the dashboard loads in default mode with no URL parameters
- **THEN** a banner is pinned to viewport top with LEFT=`UNCLASSIFIED`,
  CENTER starting `EXERCISE: OVERWATCH-26 ·`, RIGHT=`OPS-1`
- **AND** an identical banner is pinned to viewport bottom (with the
  Expanso wordmark added to its RIGHT region)
- **AND** both banners use mono uppercase styling
- **AND** the classification text uses the amber accent color

#### Scenario: Banners do not change the existing grid layout

- **WHEN** the banners are added
- **THEN** the existing grid in `DEMO_UI_SPEC.md` §2 (header → trigger
  bar → camera tiles → event panels → platform/egress → footer) is
  unchanged in row order or proportions
- **AND** the banners occupy viewport space outside the grid container,
  not within it

#### Scenario: Real classification level is rejected

- **GIVEN** a developer attempts to set the LEFT slot to `SECRET`,
  `TOP SECRET`, `CONFIDENTIAL`, or any classification above
  `UNCLASSIFIED`
- **WHEN** the change is reviewed
- **THEN** it is rejected
- **AND** the dashboard never displays a real classification level

### Requirement: Operator callsign defaults to OPS-1 and is configurable via URL parameter

The dashboard SHALL render the operator callsign in the RIGHT slot of
both classification banners, defaulting to the literal string `OPS-1`
and accepting an `op` URL query parameter to override the default.
The provided value SHALL be upper-cased, then validated against the
regex `^[A-Z0-9-]{1,12}$`. On match, the override SHALL be used. On
mismatch or empty value, the default `OPS-1` SHALL be used and a
warning SHALL be logged to the browser console.

#### Scenario: No URL parameter falls back to default

- **GIVEN** the dashboard is loaded at `/edge/` with no `op` query
  parameter
- **WHEN** both classification banners render
- **THEN** the RIGHT slot of each banner reads `OPS-1`

#### Scenario: Valid URL parameter overrides default

- **GIVEN** the dashboard is loaded at `/edge/?op=SIERRA-7`
- **WHEN** both classification banners render
- **THEN** the RIGHT slot of each banner reads `SIERRA-7`

#### Scenario: Lowercase URL parameter is upper-cased

- **GIVEN** the dashboard is loaded at `/edge/?op=sierra-7`
- **WHEN** both classification banners render
- **THEN** the RIGHT slot of each banner reads `SIERRA-7`
- **AND** the upper-casing happens before regex validation

#### Scenario: Invalid URL parameter falls back to default

- **GIVEN** the dashboard is loaded at `/edge/?op=bad value!` or
  `/edge/?op=` (empty) or `/edge/?op=TOO-LONG-CALLSIGN-VALUE`
- **WHEN** both classification banners render
- **THEN** the RIGHT slot of each banner reads `OPS-1`
- **AND** a warning is logged to the browser console naming the
  rejected value

### Requirement: Data density meets the operational target at both stage and laptop scale

The dashboard SHALL meet a tiered density floor:

- **Stage tier (viewport ≥1920×1080):** at least 30 distinct,
  machine-readable data points within the visible viewport in default
  state.
- **Laptop tier (viewport ≤1700px wide):** at least 18 distinct,
  machine-readable data points within the visible viewport in default
  state.

The vocabulary primitives defined in this spec MUST remain legible at
laptop scale via the existing `@media (max-width: 1700px)` token
override in `public/edge/styles.css`.

The implementation MUST surface, at minimum (subject to graceful
condensation at laptop scale):

- Per-sector frame rate (e.g., `30 FPS`)
- Per-sector sensor type (e.g., `EO/IR · 1280×720`)
- Per-sector link RSSI (placeholder permitted; e.g., `-62 dBm`)
- Per-sector frames-since-last-event counter
- Mission elapsed time (`T+HH:MM:SS`, monotonic from orchestrator
  start)
- Total bytes archived to S3 (e.g., `14.7 MB`)
- Cloud RTT to S3 (placeholder permitted; e.g., `84 ms`)
- Cumulative contact totals since mission start (e.g., `PERSON: 247
  · BACKPACK: 88 · DRONE: 3`)
- Orchestrator uptime
- SQLite event-store row count

These are in ADDITION to the existing fields (events/min, fused
total, jobs n/m, total events, DBOM-signed total, recent S3 keys list,
trigger classes, current cloud-link state, per-sector last-event
timestamp, recent event rows).

#### Scenario: Stage viewport contains ≥30 data points

- **WHEN** the dashboard is rendered at 1920×1080 in default fake-mode
- **THEN** an enumeration of distinct numeric/categorical data points
  visible above the fold totals at least 30
- **AND** the count is reproducible across reloads (within ±2 due to
  list lengths)

#### Scenario: Laptop viewport contains ≥18 data points

- **WHEN** the dashboard is rendered at a viewport ≤1700px wide
  (e.g., 1366×768) in default fake-mode
- **THEN** an enumeration of distinct numeric/categorical data points
  visible above the fold totals at least 18
- **AND** every visible vocabulary primitive (`display-rectangle`,
  `bracket-label`, `led-status`, `metric-block`, `data-row`,
  `divider-line`, `frame-corner`) remains legible at this scale

#### Scenario: Placeholder fields are documented as such

- **GIVEN** the orchestrator surfaces a placeholder value for link
  RSSI or RTT (no real radio/network instrumentation)
- **WHEN** the corresponding `/metrics` endpoint is inspected
- **THEN** the field's docstring or schema explicitly marks it as a
  deterministic placeholder
- **AND** the placeholder value is stable across reloads (does not
  jitter randomly per-frame)

### Requirement: Vocabulary spec complements but does not replace `DEMO_UI_SPEC.md`

This vocabulary spec SHALL coexist with `DEMO_UI_SPEC.md`. Where the
two overlap, this spec is more specific and supersedes for shape,
hierarchy, language, time format, mission chrome, and density. The
layout grid (§2), color tokens (§1.3), type scale (§1.2), motion
budget (§1.4), and per-tile behavior (§3-§5) of `DEMO_UI_SPEC.md` SHALL
remain in force unchanged.

#### Scenario: Color tokens unchanged

- **WHEN** any element styled by this vocabulary is rendered
- **THEN** the colors used are drawn only from the existing palette in
  `DEMO_UI_SPEC.md` §1.3
- **AND** no new color token is introduced

#### Scenario: Grid topology unchanged

- **WHEN** the dashboard is rendered with the new vocabulary applied
- **THEN** the row order, row proportions, and column split defined in
  `DEMO_UI_SPEC.md` §2 are preserved
- **AND** the only viewport-level addition is the top/bottom
  classification banner OUTSIDE the grid container

#### Scenario: WebSocket protocol unchanged

- **WHEN** the dashboard is rendered with the new vocabulary applied
- **THEN** the WebSocket message shapes consumed from the orchestrator
  are byte-identical to those consumed before the vocabulary change
- **AND** any new fields surfaced come from additive `/metrics`
  fields, not from changes to `/events` or `/ws` payloads

### Requirement: Vocabulary compliance is detectable in smoke tests

The dashboard's static assets SHALL contain stable sentinels that the
smoke-test suite can use to assert the vocabulary is wired (without
running a browser).

#### Scenario: Smoke tests confirm primitive classes and banner exist

- **WHEN** the smoke test inspects `public/edge/index.html`
- **THEN** it finds at least one element using each of the seven
  primitive classes (`display-rectangle`, `bracket-label`,
  `led-status`, `metric-block`, `data-row`, `divider-line`,
  `frame-corner`)
- **AND** it finds the classification banner markup containing the
  literal default strings `UNCLASSIFIED` and `EXERCISE: OVERWATCH-26`
- **AND** it confirms `public/edge/ws_client.js` exports or defines a
  `formatIsoUtc` function
- **AND** it confirms `toLocaleTimeString` does NOT appear in
  operational call sites within `ws_client.js`
- **AND** it confirms the strings `DTG`, `Zulu`, and `Shift+Z` do
  NOT appear in `public/edge/ws_client.js` or `public/edge/index.html`
- **AND** it confirms the bottom banner contains an Expanso wordmark
  reference (an `<img>` to `expanso-wordmark.svg` or a CSS-drawn
  `EXPANSO` element)
- **AND** it confirms the textual slogan `Powered by Expanso ·
  workload moves to the data` is absent from the rendered HTML
