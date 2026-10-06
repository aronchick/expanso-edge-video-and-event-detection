# Design brief: Edge ISR dashboard

## Subject material

Perimeter intelligence, surveillance and reconnaissance (ISR). Two fixed
cameras watch two zones; the edge counts people in each, merges the counts, and
raises a flag when the combined total crosses a threshold. The visual material
is survey paper, plotted gates and range marks: a working instrument, not a
product page. The camera feeds are the only photographic content and sit in
neutral near-black tiles so they read the same in either theme.

## Palette and why

Per `../AGENTS.md` (Theme): the board is a standalone surface, so it carries its
own ground, and the palette is drawn from the subject, not from another demo.

- Ground: cool survey paper (`#e8ece8` bg, `#f7f9f7` surface), never pure white.
  Light is the default because the booth and review settings are bright rooms
  and the board is read at a distance; dark is an explicit toggle.
- Accent, the Expanso element (edge, tabs, links): deep teal (`#0a5a63` light,
  `#6fc7cf` dark). Teal is the color of a sensing sweep; it is not the violet
  that was here before.
- Destination (Expanso Cloud, analyst gateway, S3 archive): slate blue
  (`#2c4f80` light, `#8fb2e0` dark).
- Alert state: signal red (`#b3261e` text, `#c62f25` fill). Red appears only on
  alert states: the crowd flag, drone, a down cloud link, an active alert.
- Class and status colors carry meaning, never decoration: person and live are
  green, backpack and pending are amber, drone and failed are red. Each has a
  dark text variant for light mode and a light variant for dark mode.
- No gradients, no drop shadows, no grid or dot-pattern backgrounds, no side
  stripes on cards. The only glows are the alert pulses.

## Type

Self-hosted IBM Plex Sans for text and IBM Plex Mono for counts, labels and
data (`public/edge/fonts`, a copy of `public/fonts` because the orchestrator
serves only `public/edge`). Sizes come off one scale (13, 15, 18, 22, 32 px,
plus clamped counts); radii off three steps (4px, 8px, pill). Headings are
sentence case; there are no small uppercase kickers above headings.

## The two themes

One token block per theme in `styles.css` (`:root` and
`:root[data-theme="dark"]`). `theme.js` sets `data-theme` before first paint
from `localStorage` (inside try/catch) and ignores the OS color-scheme
preference. The header button `#theme-toggle` has `aria-pressed`. The ARCH
diagram, camera chrome and overlays follow the chosen theme; the camera tile
overlays use one opaque near-black chip in both.

## Layout

From 1100 x 720 up it is a one-screen stage (cameras left sized to 16:9, tally
right, platform strip and footer pinned). Below that it is one scrolling column:
header wraps, tiles stack at 16:9, then combined, zones, events, platform
strip, footer. ARCH keeps its five-column diagram with live-geometry curves
down to 56rem, then stacks in the same order and keeps the flow labels as chips.

## Accessibility decisions

- WCAG AA contrast (4.5:1, 3:1 large) for every text pair in both themes,
  measured with `tests/browser/page-audit.js` (zero failures) and axe (zero
  violations) on OPS, ARCH, ARCHIVE and the S3 modal.
- No horizontal scroll at 320, 400, 768 and 1440 px in either theme.
- Tabs follow the ARIA pattern: Left, Right, Home and End move and activate,
  roving tabindex, visible focus ring, no scroll jump on switch, hash routing
  kept (`#ops`, `#arch`, `#archive`).
- Auto-rotate (5 s, held 45 s after a manual change) has a visible pause and
  resume button, is off under `prefers-reduced-motion` and on phone and tablet
  layouts, and motion in the diagram is hidden under reduced motion.
- Status is never color alone: every badge, pill and dot carries a text label.
- Keyboard help is a visible "Keys" button in the footer (F1 to F4, Escape).
- JSON in the archive modal is pretty-printed vertically in a wrapping `<pre>`.
- The dashboard shows a clear disconnected banner and reconnects in place; it
  never reloads the page, so scroll position and theme survive.
