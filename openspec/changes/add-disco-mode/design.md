# DiSCO Mode — Design

Lightweight, hackathon-only. Three concrete pieces, one new file, two
files modified.

## 1. Mode resolution + persistence

Single function in `ws_client.js`, called once on script load:

```javascript
function resolveDashboardMode() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("mode");          // "disco" | "imagery" | null
  const fromStorage = localStorage.getItem("dashboardMode");
  const mode = fromUrl || fromStorage || "imagery";
  document.body.dataset.mode = mode;                     // CSS hooks off this
  if (mode !== fromStorage) localStorage.setItem("dashboardMode", mode);
  if (mode !== fromUrl) {
    url.searchParams.set("mode", mode);
    history.replaceState(null, "", url);                 // sync URL silently
  }
  return mode;
}
```

`F5` handler calls `resolveDashboardMode` after flipping. `data-mode`
on `<body>` lets CSS toggle waterfall vs camera tile via simple
selectors — no per-element JS show/hide.

## 2. Label substitution

`disco-labels.js` exports a flat object:

```javascript
export const DISCO_LABELS = {
  "Sector north":              "C-Band Sensor",
  "Sector south":              "UHF Sensor",
  "events/min":                "SOI/min",
  "Trigger classes":           "SOI library v1",
  "YOLO":                      "Classifier",
  "MULTI-SECTOR CORRELATION":  "MULTI-BAND FUSION",
  "Cloud Egress · S3 archive": "SOI Egress · S3 archive",
  "events":                    "SOI hits",
  "fused":                     "fused contacts",
  // YOLO hit labels → SOI signature names
  "person":   "TYPE-A SOI",
  "backpack": "TYPE-B SOI",
  "drone":    "GROUP-1 UAS SIG",
  "airplane": "GROUP-3 UAS SIG",
  // ... operator extends as needed
};

export function discoLabel(s) {
  if (document.body.dataset.mode !== "disco") return s;
  if (DISCO_LABELS[s] !== undefined) return DISCO_LABELS[s];
  // Warn once per missing key (Set memo) so console doesn't flood.
  warnOnceMissing(s);
  return s;
}
```

`ws_client.js` wraps every user-facing string write through
`discoLabel(...)`. That's the substitution surface — about a dozen
call sites, all in `renderEvent`, `renderTriggers`, `renderJobs`,
`renderS3`, `setCloudState`, `showFused`.

## 3. Spectrum waterfall renderer

Two `<canvas>` elements added to `index.html`:

```html
<canvas id="disco-waterfall-north" class="disco-waterfall" width="800" height="450"></canvas>
<canvas id="disco-waterfall-south" class="disco-waterfall" width="800" height="450"></canvas>
```

CSS hides them in imagery mode and the camera-feed `<img>` in DiSCO
mode:

```css
body[data-mode="imagery"] .disco-waterfall { display: none; }
body[data-mode="disco"]   .sector-feed img  { display: none; }
```

Renderer (single `requestAnimationFrame` loop, both sectors share it):

```javascript
function paintWaterfall(canvas, sector, eventBursts) {
  const ctx = canvas.getContext("2d");
  // 1. Scroll existing image down by 1 px (cheap: drawImage onto self with offset)
  ctx.drawImage(canvas, 0, 1);
  // 2. Paint a new top row of noise floor (bluish-grey gradient with random jitter)
  const top = ctx.createImageData(canvas.width, 1);
  for (let x = 0; x < canvas.width; x++) {
    const noise = Math.random() * 40;
    top.data[x*4]   = 20 + noise;
    top.data[x*4+1] = 30 + noise;
    top.data[x*4+2] = 60 + noise * 1.5;
    top.data[x*4+3] = 255;
  }
  // 3. Overlay any active bursts at their frequency slots
  for (const burst of eventBursts) {
    const x = burst.freqSlot;
    const intensity = burst.energy;  // 0..1, fades over ~3s
    const w = 6 + intensity * 8;
    for (let dx = -w; dx <= w; dx++) {
      const xi = (x + dx + canvas.width) % canvas.width;
      const fade = (1 - Math.abs(dx) / w);
      const r = 255 * intensity * fade;
      const g = 200 * intensity * fade;
      const i = xi * 4;
      top.data[i]   = Math.min(255, top.data[i]   + r);
      top.data[i+1] = Math.min(255, top.data[i+1] + g);
    }
  }
  ctx.putImageData(top, 0, 0);
}
```

Each event creates a burst object with a `freqSlot` derived
deterministically from the YOLO label (`hash(label) % canvas.width`)
so the same signature hits the same column repeatedly — judges
pattern-match it visually as "the C-band emitter is active again."
Bursts decay their `energy` by ~0.012 per frame (≈3s to fade at 30fps,
which means ~80 rows of scroll, fits a 450px canvas).

## 4. What's NOT in this design

- Real spectrum data (would need an SDR + real decoder)
- Real frequency calibration (the X axis is decorative, not metric)
- Real fade curves (use a simple linear decay, looks fine)
- Configurability of waterfall colors, scroll rate, burst width
  (hardcode for the demo; operator who wants different look edits
  the code)

## 5. Smoke test extension

`tests/test_armyx_smoke.py` gains:

- `test_disco_labels_file_exists_with_required_keys`
- `test_dashboard_html_has_waterfall_canvas`
- `test_ws_client_handles_mode_param_and_f5`

All filesystem-level (grep / yaml-parse style), no browser needed.
