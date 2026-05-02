# Dashboard Design Vocabulary — Implementation Tasks

These tasks are intentionally high-level. The implementation pass that
follows this proposal will refine each into concrete CSS class names,
DOM structure changes, and orchestrator field additions.

## 1. Shape primitives — CSS scaffolding

- [ ] 1.1 Add CSS classes for the 7 named primitives in
  `public/edge/styles.css`: `.display-rectangle`, `.bracket-label`,
  `.led-status`, `.metric-block`, `.data-row`, `.divider-line`,
  `.frame-corner`. Document each at the top of the file with a
  one-line use-when comment matching the spec.
- [ ] 1.2 Audit existing class usage; deprecate (don't delete) classes
  that are now superseded so a single PR can land without breaking
  anything mid-flight.
- [ ] 1.3 Add `.frame-corner` decoration to the two sector tiles and
  the cloud-egress tile so critical zones read as bracketed.
- [ ] 1.4 Confirm every primitive remains legible under the existing
  `@media (max-width: 1700px)` token override; adjust primitive-level
  rules (not the existing tokens) if any primitive collapses at that
  breakpoint.

## 2. Status indicator system

- [ ] 2.1 Replace every pill-shaped status badge in
  `public/edge/index.html` with the LED pattern: filled-square glyph
  (Unicode `■` or a CSS `::before` square) + uppercase mono label.
- [ ] 2.2 Wire the 1Hz blink animation to critical states only (DOWN,
  FAULT, STALLED). Steady states (LIVE, STANDBY, OK) do not blink.
- [ ] 2.3 Verify color mapping uses existing tokens from
  `DEMO_UI_SPEC.md` §1.3 (no new colors).

## 3. Metric hierarchy

- [ ] 3.1 Replace each metric pill in the header (`events/min`,
  `fused`, etc.) with a `.metric-block` (label-on-top, value-dominant,
  ≥3× ratio).
- [ ] 3.2 Insert `.divider-line` between adjacent metric blocks in
  shared regions (header counter strip, footer stats strip).
- [ ] 3.3 Apply tabular-nums + the `display` size class from the
  existing type scale to every value.

## 4. Language standards

- [ ] 4.1 Capitalize every operational label in
  `public/edge/index.html` (TRIGGER CLASSES, SECTOR NORTH, RECENT,
  EXPANSO PLATFORM, CLOUD EGRESS, etc.).
- [ ] 4.2 Replace empty/error-state copy with compressed ops-language.
  Specifically: replace `add bucket via .env or expanso job` with
  `ARCHIVE: STANDBY · CONFIGURE BUCKET`; replace `last upload: —` with
  `LAST UPLOAD: —`.
- [ ] 4.3 Remove the textual marketing string `Powered by Expanso ·
  workload moves to the data` from the in-frame footer. Replacement
  is the Expanso wordmark in the bottom banner RIGHT region (see §6).
- [ ] 4.4 Reformat each event-log line to the compressed pattern
  `T+MM:SS :: N×CLASS NN% :: "caption" :: dbom:0xab12...` (truncated
  hash, mono). The leading `T+MM:SS` is mission-elapsed time, NOT a
  wall-clock timestamp; wall-clock timestamps elsewhere use ISO 8601
  UTC per §5.

## 5. ISO 8601 UTC timestamp format

- [ ] 5.1 Add an ISO 8601 UTC formatter in `public/edge/ws_client.js`:
  `formatIsoUtc(date)` → `YYYY-MM-DDTHH:MM:SSZ` (e.g.,
  `2026-05-02T10:52:13Z`). Always UTC, always extended form, always
  trailing `Z`.
- [ ] 5.2 Replace every `toLocaleTimeString()` call site with
  `formatIsoUtc(...)` for operational wall-clock timestamps (banner
  CENTER slot, event rows, egress meta, fused alert metadata, S3
  last-upload, per-sector last-event).
- [ ] 5.3 Confirm relative-age renderings (e.g., `47s ago` in the
  egress tile) are NOT converted — those are ages, not timestamps,
  and remain in their existing relative form.
- [ ] 5.4 Remove any leftover references to "DTG", "Zulu", or
  `Shift+Z` from in-tree comments, code, and `STAGE_RUNBOOK.md`.

## 6. Classification + mission banner

- [ ] 6.1 Add two banner `<div>`s pinned to viewport top and bottom in
  `public/edge/index.html`, outside the existing grid container.
- [ ] 6.2 Each banner has three slots: LEFT (classification), CENTER
  (mission codename + ISO 8601 UTC), RIGHT (operator callsign;
  bottom banner additionally hosts the Expanso wordmark, see §6.6).
- [ ] 6.3 Default content: LEFT=`UNCLASSIFIED`, CENTER=`EXERCISE:
  OVERWATCH-26 · <ISO 8601 UTC>`, RIGHT=`OPS-1`.
- [ ] 6.4 Classification text uses the existing amber accent
  (`#ffa726`); banner background uses surface dark; thin top/bottom
  borders.
- [ ] 6.5 The center timestamp updates every second from the new
  `formatIsoUtc()` helper.
- [ ] 6.6 Add an Expanso wordmark element to the bottom banner RIGHT
  region. Sizing: ≤24px tall. Color: monochrome dim white at 60-70%
  opacity. Source: prefer an SVG sourced from `https://expanso.io`
  brand assets (commit it to `public/edge/assets/expanso-wordmark.svg`).
  If no acceptable SVG is available, fall back to a CSS-drawn wordmark
  rendered in IBM Plex Mono uppercase (`EXPANSO`) at the same size and
  opacity. This file-vs-CSS choice is a task-level decision; the spec
  only requires *a* wordmark.
- [ ] 6.7 Implement operator-callsign URL parameter parsing: read the
  `op` query parameter from `window.location.search`, upper-case it,
  validate against `/^[A-Z0-9-]{1,12}$/`, use as the RIGHT-slot
  callsign in both banners. On mismatch or empty, fall back to
  `OPS-1` and emit a `console.warn` with the rejected value.

## 7. Data density target (tiered)

- [ ] 7.1 Add per-sector data inside each sector tile: frame rate
  (`30 FPS`), sensor type (`EO/IR · 1280×720`), link RSSI placeholder
  (`-62 dBm`), frames-since-last-event counter.
- [ ] 7.2 Add mission elapsed time to the top banner CENTER slot
  (`T+00:14:32`, monotonic from orchestrator start).
- [ ] 7.3 Add total bytes archived + cloud RTT to the cloud-egress
  tile.
- [ ] 7.4 Add cumulative contact totals to the footer
  (`PERSON: 247 · BACKPACK: 88 · DRONE: 3`).
- [ ] 7.5 Add orchestrator uptime + SQLite event-store row count to
  the footer.
- [ ] 7.6 Extend `/metrics` (or the relevant orchestrator endpoint)
  with the new fields. Real values where cheap (uptime, event count,
  bytes archived); deterministic placeholders where they require
  hardware (RSSI, RTT). Document each as real-or-placeholder in the
  endpoint docstring.
- [ ] 7.7 Verify the laptop tier (≤1700px wide) still meets the ≥18
  data-point floor with the same primitives. If any field collapses
  invisibly at laptop scale, condense it (e.g., fold per-sector RSSI
  into the sector tile header) rather than dropping it.

## 8. Smoke tests

- [ ] 8.1 `test_dashboard_has_classification_banner` — grep
  `index.html` for the banner markup and the literal default
  classification string `UNCLASSIFIED` and `EXERCISE: OVERWATCH-26`.
- [ ] 8.2 `test_dashboard_uses_led_status_pattern` — grep
  `index.html` for the `.led-status` class and the absence of the
  deprecated pill class on status badges.
- [ ] 8.3 `test_dashboard_uses_metric_blocks` — grep for the
  `.metric-block` class on the header counters.
- [ ] 8.4 `test_dashboard_iso_utc_formatter_exists` — grep
  `ws_client.js` for `formatIsoUtc` and confirm `toLocaleTimeString`
  no longer appears in operational call sites; also confirm the
  strings `DTG`, `Zulu`, and `Shift+Z` no longer appear in the
  dashboard sources.
- [ ] 8.5 `test_dashboard_has_density_fields` — assert the new
  density-target fields appear in `/metrics` JSON and in the rendered
  HTML.
- [ ] 8.6 `test_dashboard_callsign_url_param` — assert that loading
  the dashboard with `?op=SIERRA-7` results in the RIGHT slot showing
  `SIERRA-7`; that loading with `?op=` (empty) or `?op=bad value!`
  falls back to `OPS-1`; that loading with no `op` param shows the
  default `OPS-1`.
- [ ] 8.7 `test_dashboard_has_expanso_wordmark` — assert that the
  bottom banner RIGHT region contains either an `<img>` referencing
  `expanso-wordmark.svg` or a CSS-drawn fallback element with the
  text `EXPANSO`; assert that the textual slogan `Powered by Expanso
  · workload moves to the data` does NOT appear anywhere in
  `index.html`.

## 9. Docs

- [ ] 9.1 Add a back-reference in `DEMO_UI_SPEC.md` pointing to the
  vocabulary spec under `openspec/specs/edge-isr-dashboard/` (added
  when this change is archived).
- [ ] 9.2 Update `STAGE_RUNBOOK.md`: add a one-line note on the
  classification banner default and the `?op=` callsign override.
  Confirm no DTG/Zulu/Shift+Z keystroke rows remain.
- [ ] 9.3 Update `HACKATHON_SCRIPT.md` Beat 0 / pre-stage notes to
  mention the mission codename so the operator script matches the
  on-screen text.

## 10. Validation

- [ ] 10.1 `openspec validate refine-dashboard-design-vocabulary
  --strict --no-interactive` — must pass.
- [ ] 10.2 `uv run pytest -q` — must stay green (existing + new tests).
- [ ] 10.3 `uv run ruff check . && uv run ruff format .` — clean.
- [ ] 10.4 Manual: load dashboard at 1920×1080, count distinct data
  points, confirm ≥30. Reload at 1366×768 (laptop tier), confirm
  ≥18. Step back 15 ft (or use a phone at 6 ft per `DEMO_UI_SPEC.md`
  §6) and confirm every label is readable.
- [ ] 10.5 Re-run `frontend-aesthetics` slop-detection pass; target
  score ≥7/10 (current baseline 4/10).
- [ ] 10.6 `openspec archive refine-dashboard-design-vocabulary` after
  the demo to roll the spec into
  `openspec/specs/edge-isr-dashboard/spec.md`.
