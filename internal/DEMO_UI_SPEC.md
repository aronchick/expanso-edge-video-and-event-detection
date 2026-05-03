# Demo UI Spec — Stage-Scale Edge ISR Dashboard

**Goal:** make the demo land at 15-20 feet on a 1080p+ conference monitor, with Expanso's platform footprint unmissable in every frame.

This spec captures decisions ahead of implementation so we don't drift mid-build. Three streams: A (Expanso visible), B (dashboard rebuild), C (theatre).

---

## 1. Constraints and design principles

### 1.1 Display target
- **Primary**: 1920×1080 conference monitor or projector, ~55-75" diagonal, viewed from 15-20 ft.
- **Secondary**: 2560×1440 if the venue provides it; layout scales up gracefully via `vw`/`vh` units, no fixed pixels for hero elements.
- **Browser**: Chrome fullscreen (F11). No multi-window. No resizing during demo.

### 1.2 Type scale (stage-readable)
| Role | Size | Use |
|---|---|---|
| Hero / takeover | 96-128px | Cloud-link DOWN banner, fused alert headline |
| Display | 56-72px | Sector counters, big numbers |
| Heading | 36-44px | Sector titles, section headers |
| Body large | 28px | Event details, model version |
| Body | 22px | Event timestamps, descriptions |
| Caption | 18px | DBOM signature, footer metrics |

Minimum readable size on the dashboard: **18px**. No 12px tags, ever.

### 1.3 Color and contrast
- **Background**: deep neutral `#0b0d10` (not pure black; reduces eye strain and projector bloom)
- **Surface**: `#15181d`, `#1c2027`
- **Accent (live/ok)**: `#3ddc84` (green)
- **Accent (alert)**: `#ffa726` (amber, for fused/triggered)
- **Accent (down/critical)**: `#ff4d4f` (red, for cloud DOWN, sector offline)
- **Accent (Expanso)**: `#7fb8dc` (calm blue, used for platform/jobs UI)
- **Text primary**: `#f4f6f8` on dark surfaces
- **Text secondary**: `#9aa3ad`
- **Signature mono**: `#5b6470` (low-contrast on purpose; verifiable but doesn't compete)

WCAG AA at minimum on every text/background pair.

### 1.4 Motion
Animation budget: 200-400ms per transition. Anything longer is theatre, anything shorter is a flicker. **Never** animate continuously — that reads as a stock-trading dashboard, not an ISR system.

Specific motion moments:
- **Event arrival**: slide-in from left, fade, settle (300ms)
- **Trigger class added**: pulse + green flash (1.5s, one-shot)
- **Fused alert**: full-screen takeover scales up from center (400ms in, hold 3s, scale down 300ms)
- **Cloud link DOWN**: red banner slides in from top (250ms), red border pulse on the body (continuous while down — this one earns its continuous animation because DDIL is the demo's money shot)

---

## 2. Layout

```
┌──────────────────────────────────────────────────────────────────┐
│ HEADER  Edge ISR · Expanso        [CLOUD LINK · UP] [12 evt/min] │
├──────────────────────────────────────────────────────────────────┤
│ TRIGGER CLASSES                                                  │
│  [person] [backpack] [car] [truck] ...                           │
├──────────────────────────────────┬───────────────────────────────┤
│ SECTOR NORTH                     │ SECTOR SOUTH                  │
│ [LIVE 1280x720]                  │ [LIVE 1280x720]               │
│ [camera feed snapshot w/ bbox]   │ [camera feed snapshot w/ bbox]│
│ status badge · last event ts     │ status badge · last event ts  │
├──────────────────────────────────┼───────────────────────────────┤
│ events north (3 most recent)     │ events south (3 most recent)  │
│ - person+backpack (78%)          │ - drone (66%)                 │
│   "Adult carrying pack..."       │   "Small quadcopter..."       │
│   sig: dbom:sha256:abc123...     │   sig: dbom:sha256:def456...  │
├──────────────────────────────────┴───────────────────────────────┤
│ EXPANSO PLATFORM                                                 │
│  ● fusion-node · running         ● sensor-north · running        │
│  ● event-archive · running       ● sensor-south · running        │
├──────────────────────────────────────────────────────────────────┤
│ FOOTER  Powered by Expanso · 4/4 jobs · 0 failed · 47 evts · 3 fused │
└──────────────────────────────────────────────────────────────────┘

OVERLAYS:

[FUSED ALERT] full screen, amber-on-black:
       ⚠  MULTI-SECTOR CORRELATION  ⚠
       SECTOR NORTH: person, backpack
       SECTOR SOUTH: drone
       correlated locally · 4.2s window

[CLOUD DOWN] red full-width banner pinned at top:
       CLOUD LINK · DOWN · 14 events queued locally · YOLO still firing
```

### 2.1 Grid (CSS)
- Outer: 100vw × 100vh, `display: grid`, rows: `auto auto 2fr 1fr auto auto`
- Camera tiles fill 2fr each in row 3; aspect locked 16:9 with `object-fit: cover`
- Event panels in row 4 are scrollable lists with the 3 most recent visible above the fold
- Hero overlays use `position: fixed; inset: 0` and high z-index

---

## 3. Stream A — Expanso visible

### 3.1 Fusion node as an Expanso job (`jobs/fusion-node-job.yaml`)

> Naming: this job runs the local FastAPI process (event store +
> cross-sensor correlator + dashboard backend). It used to be called
> `orchestrator` but that name collided with Expanso's term for the
> cluster control plane (Expanso Cloud). It's now `fusion-node`.
- `Type: ops`, runs on the laptop (constraint `node_type=laptop` or no constraint, scheduled by location)
- Maps `0.0.0.0:8080` → host
- Mounts `/data/orchestrator` for SQLite + triggers.yaml persistence
- Same Docker image as sensor (or a slim variant), CMD = `edge-orchestrator`
- Gives `expanso-cli job list` four entries: `fusion-node`, `sensor-north`, `sensor-south`, `armyx-tech-event-archive`

### 3.2 Event archive pipeline (`jobs/armyx-tech-event-archive.yaml`)
- Bloblang pipeline that tails the orchestrator's events stream
- Validates DBOM signature presence; drops fused alerts and unsigned events
- Adds lineage: `archive.archived_at`, `archive.archived_by`, `archive.bucket`
- Fan-out outputs: **`aws_s3`** (primary, batched 25/2s, date-partitioned keys), local NDJSON (belt-and-suspenders), stdout (for `expanso-cli job logs`)
- Offline buffer at `/var/lib/expanso` provides store-and-forward when the Jetson WAN is down — what makes Beat 5A→5C work
- Visible work: dashboard's Cloud Egress tile polls the bucket and surfaces count + recent keys; judges can click any key to see the JSON
- Supersedes the older `jobs/event-archive-job.yaml` (local-only archive; kept for reference)

### 3.3 Jobs status surface
- Orchestrator's `/jobs` endpoint returns the current job inventory:
  - **Real mode**: shells out to `expanso-cli job list --output json` (or polls a status file)
  - **Fake mode**: returns a synthetic list with all four jobs `running`
- Dashboard polls every 2s and renders the `EXPANSO PLATFORM` tile

### 3.4 Live trigger update via Expanso (stretch)
The current path is `curl POST /triggers`. Post-MVP, we can route the same update through an Expanso config-update event so it visibly flows through the platform. For tonight: the curl call is fine, **as long as the dashboard's trigger panel and the Expanso jobs tile both update in response** so judges see the platform reacting.

### 3.5 Cloud egress tile — the Beat 5 centerpiece

Lives in row 5 column 2 of the layout, alongside the Expanso platform tile. The dashboard polls `GET /s3` every 2s and renders:

| Element | State + visual |
|---|---|
| **State badge** (top-right of tile) | `live` (green) when last upload < 15s ago. `stalled · queued at edge` (amber) when last upload > 15s. `not configured` (gray) when `ARMYX_S3_BUCKET` unset. `poll error` (red) if AWS auth fails. |
| **Big object count** | `60px` tabular-numerals figure. Animates with a `count-bump` scale pulse (`var(--ok)` green, 400ms) every time the count increases. This is the visual that makes Beat 5C land — drain visible to the back of the room. |
| **Bucket / last-upload meta** | `18px` mono caption. Bucket name (truncates if long), last-upload age (`2s ago`, `47s ago`), poll error if any. |
| **Recent keys list** | Last 10 S3 keys, hover-highlight, click opens the modal. Keys shown right-truncated (CSS `direction: rtl`) so the most-specific tail stays visible. |

### 3.6 S3 object viewer modal
Triggered by a click on any recent key. Pulls `GET /s3/object?key=...` (orchestrator proxies to S3 via boto3, capped at 64 KB).

- Card: 70vw, 80vh max, scrollable JSON body.
- Pretty-prints if the body parses as JSON (the demo case); otherwise raw text.
- Close: `×` button, click-on-backdrop, or `Esc`.
- Use during Beat 5D to show schema differences between pre-offline and post-reconnect objects (e.g. classification field added by the live pipeline edit).

---

## 4. Stream B — Dashboard rebuild

### 4.1 Camera feed tiles
- New endpoint: `GET /snapshot/{sector}` — returns the latest annotated JPEG for that sector
  - **Real mode**: reads from `snapshots/{sector}.jpg` (already written by `detect_loop.py`)
  - **Fake mode**: synthesizes a 1280x720 JPEG with the sector name + simulated bounding boxes (so the demo loop is visually identical to real mode)
- Dashboard polls each tile every 750ms (`<img src="/snapshot/sensor-north?t={epoch_ms}">`)
- Bounding boxes are baked into the JPEG by the producer; no client-side overlay logic

### 4.2 Fused alert takeover
- Full-screen overlay, fixed `inset: 0`, z-index 1000
- Headline 96px, contacts 56px each, window 28px
- Auto-dismiss after 3.5s (long enough to read, short enough to keep demo flowing)
- Concurrent fused alerts queue, never overlap

### 4.3 Cloud link state
- When **UP**: 28px badge in header, green
- When **DOWN**: full-width banner pinned to top, 56px text, red, with live count of `queued offline` events
- Body gets a 4px red border that pulses (1Hz) — peripheral vision tells judges something is wrong even if they're looking elsewhere
- DDIL story: the dashboard's offline state must read as "operational, just degraded" not "broken"

### 4.4 Event rendering
- Each event row: 22-28px main text, 18px caption for signature, full DBOM hash visible in monospace
- New events slide in from left, push older ones down
- Events with `queued_offline=true` get a left border in the violet replay accent (`#7e57c2`)
- Cap at 3 visible per sector — older scroll behind the fold (history available via API but not on screen)

### 4.5 Trigger panel
- Top of layout, full-width strip
- Each class is a 28px chip with 12-16px padding
- New class added: scales up (1→1.1) + green pulse (1.5s, one-shot)
- Removed class fades out (300ms) and collapses

### 4.6 Header counter / metrics
- Live `events/min` updates from a rolling 60s window in the orchestrator
- Updates every second, smoothed (no jumpy single-event jolts)

---

## 5. Stream C — Theatre

### 5.1 Live counters footer
- Total events: incremental
- Fused alerts: incremental, with last-fused-at timestamp
- Total DBOM-signed: same as total events (because we sign all of them) — phrased as "47 events signed and archived"
- Current jobs: "4/4 running"

### 5.2 Mini topology diagram
- 200×120 canvas in the bottom-right corner
- Three nodes: north sensor, south sensor, fusion node
- When an event is received, draw a brief arrow from sensor → fusion node (300ms fade)
- When a fused alert fires, both arrows light up amber

### 5.3 Keyboard shortcuts (operator-only)
- `F1`: **real** WAN-down on the Jetson — orchestrator SSHes the Jetson and runs `sudo nmcli radio wifi off`. Falls back to a cosmetic flag-only flip if the SSH call fails (laptop dev mode). Calls `POST /demo/wan-down`.
- `F2`: real WAN-up — `sudo nmcli radio wifi on` over SSH. `POST /demo/wan-up`.
- `F3`: trigger a one-shot fake fused alert (for rehearsal). `POST /demo/fused-test`.
- `F4`: push the prepared trigger update (`["person","backpack","drone","airplane","car","truck"]`).

These aren't shown in the UI but exist so the operator never has to alt-tab during the demo. Each has a curl backup in `STAGE_RUNBOOK.md`.

---

## 6. Verification

The demo "shows extremely well" if all of these are true:

1. From 15 ft, every text element is readable with 20/20 vision.
2. The Expanso job tile shows 4 running jobs and is visually prominent.
3. A fused alert visibly takes over the screen for 3-4 seconds.
4. WAN-down state changes the dashboard so dramatically that no one in the room could miss it.
5. Adding `drone` to triggers produces a visible, animated change that judges' eyes follow.
6. Camera tiles show real (or synthetic) live feeds with bounding boxes.
7. DBOM signatures are readable, not decoration.
8. The dashboard runs on `--fake --multi` end-to-end and looks identical to the real-camera version (modulo the snapshot content).

Test from a phone screen propped 6 ft away. If it's readable there, it'll be readable on a 55" monitor at 15 ft.

---

## 7. Build order

Highest-leverage first (so partial completion still ships a better demo):

1. CSS design system + new layout shell
2. Camera tile endpoint + fake-mode snapshot synthesis
3. Stage-scale event rendering
4. Big cloud-link state (DDIL banner + queued counter)
5. Full-screen fused alert
6. Trigger panel rebuild (top, big chips, animated)
7. Expanso jobs tile + `/jobs` endpoint
8. Footer metrics + `/metrics` endpoint
9. Wrap fusion node as Expanso job (`jobs/fusion-node-job.yaml`)
10. S3 event archive Bloblang pipeline (`jobs/armyx-tech-event-archive.yaml`)
11. Mini topology
12. Keyboard shortcuts
13. WAN-toggle endpoints (`/demo/wan-down`, `/demo/wan-up`)

Verify after each major item with the `--fake --multi` smoke test.
