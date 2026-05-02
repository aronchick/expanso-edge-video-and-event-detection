## Why

A `frontend-aesthetics` slop-detection pass scored the current Edge ISR
dashboard 4/10. The codified design system in `DEMO_UI_SPEC.md` already
locks in good fundamentals — IBM Plex, restrained semantic palette,
tabular numerals, the cloud-down red banner — but the implementation
has drifted into generic SaaS observability shapes that undermine the
operational/military framing the demo needs in front of an Army RFI
audience.

Concretely, the live dashboard has:

- **Shape monoculture.** Every element — sector tiles, status badges,
  metric counters, trigger chips, event cards, egress tile — is the
  same rounded rectangle (4-10px radius). No shape vocabulary, so the
  eye has nothing to anchor on. ~24 pill-shaped elements visible in
  one viewport reads as a generic Datadog clone, not an ISR console.
- **Wrong metric hierarchy.** Label and value in metric pills are the
  same size and weight (`events/min 119`), so the eye treats them as
  peers instead of label→datum.
- **Friendly/SaaS copy.** Strings like `add bucket via .env or expanso
  job`, the marketing footer `Powered by Expanso · workload moves to
  the data`, civilian timestamps `10:52:13 AM`, and lowercase status
  badges (`live`, `standby`) read as a SaaS landing page, not an
  operational console.
- **No mission context.** No classification banner, no UTC timestamps,
  no exercise/mission identifier, no operator callsign — all of which
  are default chrome on every real ISR or COP UI.
- **Low data density.** Roughly 12 distinct numbers fit in the current
  1920×1080 frame; ops dashboards of this class typically carry
  30-50+. The grid topology has the room; the vocabulary doesn't use it.

This change adds a **design vocabulary delta** that complements (does
NOT replace) `DEMO_UI_SPEC.md`. The existing spec covers layout, type
scale, color, and motion. The vocabulary delta covers *shape*,
*hierarchy*, *language*, *time format*, *mission chrome*, and *data
density* — the missing axis that lets the implementation read as
operational instead of generic.

## What Changes

- **Add a shape primitive vocabulary** of 7 named primitives
  (`display-rectangle`, `bracket-label`, `led-status`, `metric-block`,
  `data-row`, `divider-line`, `frame-corner`) with a strict rule for
  when each is used. Replaces the current monoculture of rounded pills.
- **Add a status indicator system** that replaces all pill-shaped
  status badges with filled-square LED indicators + uppercase mono
  labels (`■ LIVE`, `■ STANDBY`, `■ DOWN`), with a 1Hz blink rule for
  critical/down states.
- **Add a metric hierarchy rule** requiring label-on-top + dominant
  value with a ≥3× size ratio between label and value, vertical
  dividers between adjacent metrics (no pill containers).
- **Add language standards** requiring UPPERCASE labels, compressed
  ops-language for empty/error states (e.g., `ARCHIVE: STANDBY ·
  CONFIGURE BUCKET` instead of `add bucket via .env or expanso job`),
  compressed event-log copy, and removal of the textual marketing
  slogan from inside the operational frame.
- **Add an ISO 8601 UTC timestamp requirement** — replaces all civilian
  `10:52:13 AM` timestamps with the extended UTC form (e.g.,
  `2026-05-02T10:52:13Z`) everywhere in the dashboard. No format toggle.
- **Add a classification + mission banner** at top and bottom of the
  viewport (LEFT: classification, CENTER: mission codename + ISO 8601
  UTC timestamp, RIGHT: operator callsign), with `UNCLASSIFIED //
  EXERCISE: OVERWATCH-26` as the demo default. Operator callsign
  defaults to `OPS-1` and is overridable via `?op=<CALLSIGN>` URL
  query parameter.
- **Add a data density target** with two viewport tiers:
  - Stage (≥1920×1080): floor of 30 distinct data points per viewport.
  - Laptop preview (≤1700px wide): floor of 18 distinct data points
    per viewport.
  Includes a concrete checklist of fields the implementation must
  surface (per-sector frame rate + sensor type, link RSSI, mission
  elapsed time, total bytes archived, cloud RTT, contact totals since
  mission start). Vocabulary primitives must remain legible at laptop
  scale via the existing `@media (max-width: 1700px)` token override
  in `public/edge/styles.css`.
- **Replace the textual "Powered by Expanso" slogan with the Expanso
  logo (wordmark) placed in the bottom banner RIGHT-side region.**
  Logo is monochrome, ≤24px tall, dim white at 60-70% opacity. No
  marketing text remains inside the operational frame.

## Non-Goals

This change explicitly does NOT:

- Alter the color tokens or WCAG contrast guarantees in
  `DEMO_UI_SPEC.md` §1.3. Colors stay; only shape/hierarchy/copy
  changes.
- Change the grid topology in `DEMO_UI_SPEC.md` §2 (header → trigger
  bar → camera tiles → event panels → platform/egress row → footer).
  New chrome (classification banners) is added at viewport top/bottom
  outside the existing grid.
- Modify the WebSocket protocol, REST endpoints, or any orchestrator/
  sensor backend behavior beyond optional additive `/metrics` fields.
- Specify CSS, HTML structure, or JavaScript implementation details —
  the vocabulary is framework-agnostic; implementation lives in tasks.
- Touch the `disco-mode-dashboard` capability. The vocabulary applies
  to the imagery (default) skin; how/whether DiSCO mode adopts it is a
  follow-on decision. Future demo-related vocabulary work folds into
  the `edge-isr-dashboard` capability defined here.
- Re-prioritize stage-scale viewing. When stage-scale (15-20 ft) and
  laptop-preview (≤1700px wide) conflict, stage-scale wins; both
  density floors must still be met within their respective viewport
  tier.

## Capabilities

### New Capabilities

- `edge-isr-dashboard`: the shape, status, hierarchy, language,
  time-format, mission-chrome, and density rules that complement
  `DEMO_UI_SPEC.md`'s layout/color/type spec. Defines what primitives
  the implementation may use and when. This is the canonical capability
  for future Edge ISR dashboard vocabulary work; the parallel
  in-flight `disco-mode-dashboard` capability remains separately scoped
  for the DiSCO-mode reskin and is unchanged by this proposal.

### Modified Capabilities

<!-- None. The existing dashboard layout/color/type spec lives in
     DEMO_UI_SPEC.md (a markdown reference, not yet an openspec
     capability). The disco-mode-dashboard capability introduced by
     the pending add-disco-mode change is orthogonal — it reskins
     labels and swaps camera tiles for waterfalls, but doesn't
     define shape/hierarchy/language primitives. This change adds a
     new layer of vocabulary requirements without modifying disco-mode. -->

## Decisions confirmed

The six decisions previously listed as open have been confirmed:

1. **Capability naming.** Folded into a single capability
   `edge-isr-dashboard`. The parallel `disco-mode-dashboard` capability
   from `add-disco-mode` is left untouched; future demo vocabulary
   work belongs under `edge-isr-dashboard`.
2. **Mission codename.** Demo default literal remains
   `EXERCISE: OVERWATCH-26`.
3. **Density target.** Two viewport tiers: 30 points at stage
   (≥1920×1080), 18 points at laptop scale (≤1700px wide).
4. **Operator callsign.** Default `OPS-1`; overridable via
   `?op=<CALLSIGN>` URL query parameter. Sanitized to
   `^[A-Z0-9-]{1,12}$` after upper-casing; invalid input falls back
   to `OPS-1`.
5. **Powered by Expanso.** Textual slogan removed from the operational
   frame; replaced with the Expanso wordmark/logo (≤24px tall,
   monochrome dim white at 60-70% opacity) placed in the bottom
   banner RIGHT region.
6. **Timestamps.** ISO 8601 extended UTC (`2026-05-02T10:52:13Z`)
   everywhere; no DTG format, no Zulu/local toggle, no `Shift+Z`
   keybinding. Relative ages (e.g., `47s ago`) are not timestamps and
   stay as-is.

## Impact

- **Code**: `public/edge/styles.css` (new primitive classes, no
  removal of color tokens); `public/edge/index.html` (classification
  + mission banner markup at viewport top/bottom; new metric block
  structure replacing metric pills; LED status indicator markup;
  Expanso wordmark in bottom banner RIGHT region); `public/edge/ws_client.js`
  (ISO 8601 UTC formatter, callsign URL-param parser, new
  density-target fields wired from `/metrics`); orchestrator
  `/metrics` endpoint may gain fields (link RSSI placeholder, mission
  elapsed time, bytes archived, RTT) but its existing fields stay
  intact.
- **Docs**: `DEMO_UI_SPEC.md` gains a back-reference pointer to the
  vocabulary spec; `STAGE_RUNBOOK.md` is unchanged regarding time
  formats (no toggle to document).
- **Tests**: extend `tests/test_armyx_smoke.py` with file-level
  assertions that the classification banner markup, the LED status
  classes, the ISO 8601 UTC formatter call sites, the callsign URL
  parameter handler, the Expanso logo asset reference, and the new
  metric-block structure exist.
- **No** AWS, Expanso job spec, Jetson, or sensor-side changes.
- **No** breaking changes to the WebSocket protocol or REST API
  shapes that consumers outside the dashboard depend on.
- **Operator workflow**: unchanged. Same F1/F2/F3/F4 keystrokes, same
  beats. The dashboard simply *looks* operational instead of generic.
