# DiSCO Mode — Implementation Tasks

## 1. Mode resolution + persistence

- [ ] 1.1 Add `resolveDashboardMode()` to `public/edge/ws_client.js`; call once on script load and again on F5
- [ ] 1.2 Set `document.body.dataset.mode` from the resolved mode so CSS can hook off `body[data-mode="disco"]`
- [ ] 1.3 Persist mode to `localStorage` AND keep URL `?mode=...` in sync via `history.replaceState`
- [ ] 1.4 Bind `F5` keydown to flip mode + re-resolve, with the same input-element guard the other operator keys use

## 2. Label substitution

- [ ] 2.1 Create `public/edge/disco-labels.js` with the `DISCO_LABELS` flat object and `discoLabel()` helper
- [ ] 2.2 Include it from `public/edge/index.html` BEFORE `ws_client.js` (script tag, no module bundler)
- [ ] 2.3 Wrap user-facing string writes in `ws_client.js` through `discoLabel(...)` — call sites: sector titles, header counters, footer stats, event card YOLO label, trigger chips, fused alert headline + sector names, cloud-egress label, cloud-banner text
- [ ] 2.4 `warnOnceMissing(s)` memoizer so unmapped labels don't spam the console

## 3. Spectrum waterfall

- [ ] 3.1 Add two `<canvas id="disco-waterfall-{north,south}">` elements to `public/edge/index.html`, positioned absolutely over each `.sector-feed`
- [ ] 3.2 Add CSS rules to `public/edge/styles.css` to show waterfall + hide camera image only when `body[data-mode="disco"]`
- [ ] 3.3 Implement the `paintWaterfall()` renderer per design.md (drawImage scroll + noise floor + burst overlays); single `requestAnimationFrame` loop driving both canvases
- [ ] 3.4 Wire event arrivals to push burst objects into the per-sector active-bursts array; deterministic `freqSlot` from a hash of the label
- [ ] 3.5 Energy decay per frame (~0.012/frame at 30fps) so bursts fade out as they scroll downward; remove from active list when `energy <= 0`

## 4. Smoke tests

- [ ] 4.1 `test_disco_labels_file_exists_with_required_keys` in `tests/test_armyx_smoke.py` — assert the export object has the canonical keys (per spec)
- [ ] 4.2 `test_dashboard_html_has_waterfall_canvas` — grep for the two canvas IDs in `index.html`
- [ ] 4.3 `test_ws_client_handles_mode_param_and_f5` — grep for `mode=disco`, `localStorage`, and an `F5` case in `ws_client.js`

## 5. Docs

- [ ] 5.1 Add a row to `STAGE_RUNBOOK.md` keystroke table for `F5` (toggle DiSCO mode) with curl-equivalent instructions ("reload with `?mode=disco`")
- [ ] 5.2 Add one paragraph to `HACKATHON_SCRIPT.md` Beat 0 / pre-stage notes on the EW framing — when to flip mode (default to imagery; flip if the room reads EW-leaning)
- [ ] 5.3 Update `README.md` operator-shortcuts list with F5

## 6. Validation

- [ ] 6.1 Run `openspec validate add-disco-mode` and resolve any shape errors
- [ ] 6.2 Run `uv run pytest -q` — must stay green (existing 162 + 3 new)
- [ ] 6.3 Run `uv run ruff check .` and `uv run ruff format .` — clean
- [ ] 6.4 Manual: load dashboard with `?mode=disco`, then `?mode=imagery`, then default; press F5 from each; verify waterfall renders bursts on simulated events
- [ ] 6.5 `openspec archive add-disco-mode` after the demo to roll the spec into `openspec/specs/disco-mode-dashboard/spec.md`
