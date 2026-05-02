# Dashboard Design Vocabulary — Design

## Context

`DEMO_UI_SPEC.md` is the canonical layout/color/type spec for the Edge
ISR dashboard, written for stage-scale viewing (1920×1080 at 15-20 ft).
It defines the grid, the color palette, the type scale, the motion
budget, and per-tile behavior. It does *not* define a shape vocabulary,
a metric hierarchy, language standards, time format, mission chrome,
or a data-density target — and the implementation has drifted into
generic SaaS shapes as a result.

This change adds a vocabulary layer on top of `DEMO_UI_SPEC.md`. The
two specs are designed to be read together: layout from the original,
vocabulary from this one. Where they overlap (e.g., chip styling), the
vocabulary spec is more specific and supersedes.

The audience constraint is non-obvious: this dashboard goes in front
of an Army RFI panel, but is also frequently demoed on a laptop. The
spec must therefore hold up at both stage-scale (1920×1080, 15-20 ft)
AND laptop-preview scale (≤1700px wide, ~2 ft). Generic SaaS
observability vocabulary actively undermines the demo's credibility —
judges read it as "civilian prototype, not a real ops UI." The fix is
not just visual taste; it is *genre-correct vocabulary* that signals
"this would not look out of place in a TOC."

## Goals / Non-Goals

**Goals**
- Define a small, named, reusable shape vocabulary (≤7 primitives)
  with a strict use-when rule for each.
- Replace pill-shaped status badges with a literal LED indicator
  pattern matching real ops UIs.
- Establish a metric hierarchy (label-on-top, value-dominant) with a
  ≥3× size ratio.
- Replace friendly/SaaS copy with compressed ops-language; add a
  mission/classification banner and a single ISO 8601 UTC time format.
- Set a tiered data-density target (≥30 distinct data points per
  1920×1080 stage viewport; ≥18 within the ≤1700px laptop tier) with
  a concrete checklist of fields to add.
- Keep the Expanso brand inside the operational frame as a discreet
  monochrome wordmark, not as marketing copy.

**Non-Goals**
- No new color tokens (existing palette is good).
- No grid topology change (keep `DEMO_UI_SPEC.md` §2 layout intact).
- No CSS, HTML, or JS specifics — implementation is left to
  `tasks.md` and the eventual code change.
- No real classification authority — the demo defaults to
  `UNCLASSIFIED // EXERCISE: OVERWATCH-26` and never displays a real
  classification level.
- No DiSCO-mode interaction — the vocabulary applies to the imagery
  skin; how/whether DiSCO adopts it is a follow-on decision under the
  separately-scoped `disco-mode-dashboard` capability.

## Decisions

### Decision: Single capability `edge-isr-dashboard`

This vocabulary spec lands as a capability named `edge-isr-dashboard`.
Future demo-related vocabulary work folds into the same capability
rather than spawning sibling capabilities like
`edge-isr-dashboard-vocabulary`.

**Rationale (per user direction):** avoids capability sprawl as future
demo-related vocab work lands. The parallel in-flight
`disco-mode-dashboard` capability (from the `add-disco-mode` change)
remains separately scoped because its scope is the DiSCO reskin
specifically; whether to fold it in later is a separate change, not
this one.

**Alternatives considered:**
- *Sibling capabilities (`edge-isr-dashboard-vocabulary`,
  `edge-isr-dashboard-density`, etc.).* Rejected — capability sprawl
  with no clean partition story.
- *Fold disco-mode in too.* Rejected — disco-mode is in flight; touching
  it from this change would conflict.

### Decision: Seven shape primitives, no more

A controlled vocabulary of 7 named primitives (`display-rectangle`,
`bracket-label`, `led-status`, `metric-block`, `data-row`,
`divider-line`, `frame-corner`) with documented use-when rules.

**Alternatives considered:**
- *Three primitives only.* Too few — couldn't distinguish a metric
  from a status indicator from a list row, which is the core problem.
- *Open-ended primitive set.* Defeats the purpose; the current
  problem is that anything-goes shape selection produced a monoculture.
- *Material/Tailwind component vocabulary.* Wrong genre. Brings
  exactly the SaaS shapes we are trying to escape.

### Decision: LED-style status indicators (filled square + uppercase mono label)

Replaces all pill-shaped status badges. Filled square at ~0.6em next
to an uppercase mono label, separated by a single space:
`■ LIVE`, `■ STANDBY`, `■ DOWN`. Critical states (DOWN, FAULT) blink
at 1Hz to draw peripheral attention.

**Alternatives considered:**
- *Keep pills but recolor.* Doesn't solve the shape monoculture.
- *Use Unicode traffic lights.* Renders inconsistently across browsers/
  projectors and reads as casual.
- *Minimalist colored dot only, no label.* Loses information at
  15-20 ft viewing distance; unacceptable per `DEMO_UI_SPEC.md` §1.2's
  "no 12px tags, ever" principle.

### Decision: Metric blocks, not metric pills

Label-on-top (caption mono, uppercase, dim, letter-spaced 0.05-0.1em),
value below in display-sized tabular numerics. Label-to-value size
ratio ≥3×. When multiple metrics share a region, separate with thin
vertical dividers (`divider-line` primitive), not pill containers.

**Rationale:** the eye-tracking research is unambiguous — same-size
label+value in a pill containers reads as a tag, not a measurement.
A 3× ratio with the value dominant is the standard for ops UIs
(NORAD, ATC, military C2 systems all use this pattern).

**Alternatives considered:**
- *2× ratio.* Tested visually; still too tag-like at 15-20 ft.
- *Label-below-value (Bloomberg style).* Works for finance but reads
  as "ticker," wrong genre for ISR.

### Decision: ISO 8601 UTC timestamps everywhere, no toggle

All operational timestamps render as ISO 8601 extended UTC, e.g.,
`2026-05-02T10:52:13Z`. This applies uniformly to the classification
banner, event log rows, fused-alert metadata, S3 last-upload timestamp,
and per-sector last-event timestamp. No DTG (`021052Z MAY 26`) format,
no Zulu/local toggle, no keybinding.

Relative ages (e.g., `47s ago` in the egress tile) are not timestamps
and remain in their existing relative form.

**Rationale (per user direction):** ISO 8601 UTC is unambiguous,
universally parseable, and consistent with the dashboard's machine-log
character. A single canonical format eliminates the toggle decision
surface and removes the risk of mixed formats appearing in the same
viewport. The earlier DTG (`DDHHMMz MMM YY`) proposal was rejected by
the user in favor of this single uniform format.

**Alternatives considered:**
- *DTG (`021052Z MAY 26`).* Rejected by user; previously specified but
  superseded by this decision.
- *Local time with timezone label.* Rejected; mixes timezones across
  audiences.
- *ISO 8601 + DTG toggle.* Rejected; the toggle was the source of
  prior complexity and is explicitly dropped.

### Decision: Classification + mission banner at top AND bottom

Two thin (~24-32px) full-width strips, one pinned to viewport top,
one to viewport bottom. Structure (both strips identical except for
the bottom banner's logo placement, see "Expanso wordmark" decision):

```
LEFT: classification level | CENTER: mission codename · ISO 8601 UTC | RIGHT: operator callsign
```

Styling: dark gray background, mono uppercase, classification level
in amber (`#ffa726` from existing palette), thin top/bottom borders.

**Demo default:** `UNCLASSIFIED // EXERCISE: OVERWATCH-26 · 2026-05-02T10:52:13Z · OPS-1`

**Mission codename:** `EXERCISE: OVERWATCH-26` (per user
confirmation).

**Rationale:** every real ISR/COP UI has these. Their absence is
itself a signal that the dashboard is not from that world.

**Alternatives considered:**
- *Top banner only.* Real UIs always do both — bottom banner reinforces
  classification when the operator's eyes drift to the lower half.
- *Real classification authority.* Out of scope; demo never displays
  anything above UNCLASSIFIED.

### Decision: Operator callsign configurable via `?op=` URL query param

Default callsign is `OPS-1`. The dashboard accepts `?op=<CALLSIGN>` to
override. Input is upper-cased then validated against the regex
`^[A-Z0-9-]{1,12}$`. On match, the override is used in both banners.
On mismatch (or empty), the default `OPS-1` is used and a console
warning is logged.

**Rationale (per user direction):** stage demos and rehearsals often
run multiple operator personas; baking `OPS-1` into HTML forces an
edit cycle for each rehearsal. A query-param override is a five-minute
implementation that respects the operational chrome.

**Alternatives considered:**
- *Hardcoded only.* Rejected by user.
- *Server-side config (env var).* Rejected; over-engineered for a
  cosmetic value, and would require orchestrator restarts.
- *Free-form input.* Rejected; the regex prevents banner-spoofing
  injection and keeps the visual length predictable.

### Decision: Tiered density target (30 stage / 18 laptop)

Two viewport tiers carry different floors:

- **Stage tier (≥1920×1080):** ≥30 distinct data points per viewport.
- **Laptop tier (≤1700px wide):** ≥18 distinct data points per
  viewport. The same vocabulary primitives must remain legible at
  laptop scale via the existing `@media (max-width: 1700px)` token
  override in `public/edge/styles.css`.

Concrete checklist of fields the implementation must add to hit the
stage floor:

1. Per-sector frame rate (e.g., `30 FPS`)
2. Per-sector sensor type (e.g., `EO/IR · 1280×720`)
3. Per-sector link RSSI (placeholder if no real radio: `-62 dBm`)
4. Mission elapsed time (`T+00:14:32`)
5. Total bytes archived (`14.7 MB`)
6. Cloud RTT to S3 (`84 ms`)
7. Contact totals since mission start (`PERSON: 247 · BACKPACK: 88
   · DRONE: 3`)
8. Per-sector frames-since-last-event counter
9. Orchestrator uptime
10. SQLite event-store row count

Together with existing counters (events/min, fused, jobs n/m, total
events, DBOM signed) this comfortably exceeds 30 at stage scale and
18 at laptop scale.

**Rationale (per user direction):** density is the cheapest credibility
signal. A sparse ops dashboard reads as a prototype; a dense one reads
as mission-critical. The two-tier floor avoids a stage-only spec —
the user explicitly asked the laptop case to be planned for. Both
floors must be met within their viewport tier; the implementation is
expected to drop or condense fields gracefully (e.g., fold per-sector
RSSI into the sector tile header) at laptop scale.

**Alternatives considered:**
- *Single 30 floor.* Rejected by user; would force a stage-only
  design.
- *Single 50 floor.* Diminishing returns; risk of clutter.
- *No density target.* Leaves the genre-mismatch problem unfixed.

### Decision: Expanso wordmark in bottom banner RIGHT region

The textual marketing slogan `Powered by Expanso · workload moves to
the data` is removed from the operational frame entirely. In its
place, the Expanso wordmark/logo appears in the bottom banner's
RIGHT-side region, sized to ≤24px tall, monochrome (single tone) at
60-70% opacity. The exact horizontal placement within the RIGHT
region (before or after the operator callsign) is an implementation
detail; both must remain readable.

**Rationale (per user direction):** the user wants the Expanso brand
kept in the operational chrome as a *brand mark*, not as a *call to
action*. Reducing it to a discreet wordmark removes the SaaS-marketing
register without erasing the platform credit. Caption-sized + dim
opacity ensures it does not compete with operational data.

The spec deliberately does not mandate a specific SVG file path. The
implementation will need an SVG; sourcing that asset (from Expanso
brand guidelines or fallback CSS-drawn IBM Plex Mono wordmark) is a
task-level decision in `tasks.md`, not a spec-level decision.

**Alternatives considered:**
- *Keep the slogan.* Rejected by user — reads as marketing in an
  operational frame.
- *Drop Expanso entirely from the dashboard.* Rejected by user — the
  brand should remain, just demoted.
- *Full-color logo.* Rejected — competes with semantic-color status
  indicators (`#3ddc84`/`#ffa726`/`#ff4d4f`) that the eye must read
  first.

## Risks / Trade-offs

- **Risk: vocabulary lock-in feels rigid.** → Mitigation: 7
  primitives is small enough to memorize; the spec calls out that
  unlisted shapes are explicitly out of bounds, which is the point.
- **Risk: density target produces clutter.** → Mitigation: density
  checklist routes fields to specific tile regions (per-sector data
  inside sector tiles, mission data inside the new banner, archive
  data inside the egress tile). Nothing free-floats. Laptop tier
  drops the floor to 18 to prevent forced clutter at small scale.
- **Risk: military framing alienates non-defense viewers.** →
  Mitigation: the demo's audience is an Army RFI panel; the framing is
  on-target. Civilian use cases would need a separate vocabulary spec.
- **Risk: classification banner is mistaken for real classification.**
  → Mitigation: literal `UNCLASSIFIED // EXERCISE: OVERWATCH-26` makes
  the exercise/demo nature unambiguous.
- **Risk: implementation scope creep.** → Mitigation: tasks.md
  enumerates exactly which CSS classes and DOM nodes change, and the
  proposal explicitly defers CSS specifics.
- **Risk: missing Expanso SVG blocks the bottom banner.** →
  Mitigation: tasks.md includes a fallback (CSS-drawn IBM Plex Mono
  wordmark). The spec only requires *a* wordmark, not a specific file.
- **Risk: ISO 8601 reads as developer log to military audience.** →
  Acknowledged trade-off. The user explicitly chose ISO 8601 over DTG
  for cross-audience consistency; the surrounding banner chrome
  (classification, mission codename, callsign) carries the operational
  register.

## Migration Plan

This change does not migrate data or break APIs. Implementation is
purely presentational:

1. Land new CSS primitives alongside existing styles.
2. Migrate one tile at a time (sector tile → status indicators →
   metric blocks → event rows → egress tile → trigger bar → footer).
3. Add classification banners last (smallest risk surface).
4. Verify each migration with the existing `--fake --multi` smoke
   test and an in-browser visual diff at both stage (1920×1080) and
   laptop (≤1700px) viewport sizes.

Rollback: revert the `public/edge/` changes; the orchestrator changes
are additive (new optional fields in `/metrics`).

## Open Questions

- Should the `/metrics` endpoint actually surface real link RSSI on
  the Jetson (read from `iwconfig`/`nmcli`), or is a deterministic
  fake fine for the demo? *Default: deterministic fake; real radio
  data is a follow-on.*
- Where exactly within the RIGHT region of the bottom banner does the
  Expanso wordmark sit relative to the operator callsign? *Default:
  wordmark to the far right, callsign immediately left of it; both
  separated by the existing `·` separator. Confirmed at implementation
  time.*
