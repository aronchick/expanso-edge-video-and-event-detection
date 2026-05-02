// Edge ISR dashboard — stage-scale client.
// Per DEMO_UI_SPEC.md. All user-provided strings are rendered via
// textContent / createElement so the dashboard is XSS-safe.

const ws = new WebSocket(`ws://${location.host}/ws`);
const lastSeen = { 'sensor-north': 0, 'sensor-south': 0 };
const lastEventLabel = { 'sensor-north': '', 'sensor-south': '' };
const lastEventTs = { 'sensor-north': 0, 'sensor-south': 0 };
const knownTriggers = new Set();
let firstTriggerLoad = true;
let cloudUp = true;
let queuedOfflineCount = 0;
const fusedQueue = [];
let fusedShowing = false;

// Rolling 60s window of Gemini reachback timestamps (ms). Same pattern as
// `events_per_minute`, but tracked client-side: every event with a non-empty
// gemini_description means the edge chose to call the cloud analyst.
const geminiCallTimestamps = [];

// Topology canvas state
const topo = document.getElementById('topology');
const topoCtx = topo.getContext('2d');
const topoArrows = []; // { from, to, color, ttl }

// ── WebSocket handlers ──────────────────────────────────────────────

ws.onopen = () => console.log('ws connected');
ws.onclose = () => {
  console.log('ws disconnected; auto-reload in 2s');
  setTimeout(() => location.reload(), 2000);
};
ws.onmessage = (msg) => {
  const m = JSON.parse(msg.data);
  switch (m.type) {
    case 'backfill':
      m.data.slice().reverse().forEach((e) => {
        if (e.type === 'multi_sector_correlation') return; // skip backfill fused; replays would be jarring
        handleEvent(e);
      });
      break;
    case 'event':    handleEvent(m.data); break;
    case 'fused':    enqueueFused(m.data); break;
    case 'triggers': renderTriggers(m.data); break;
    case 'cloud':    setCloudState(m.data.up); break;
    case 'jobs':     renderJobs(m.data); break;
    case 'metrics':  renderMetrics(m.data); break;
    case 's3':       renderS3(m.data); break;
  }
};

// ── Events ──────────────────────────────────────────────────────────

function handleEvent(e) {
  if (!e || !e.node || e.node === 'fusion-node') return;
  const sector = e.node;
  lastSeen[sector] = Date.now();
  lastEventTs[sector] = e.ts;
  if (e.queued_offline) queuedOfflineCount += 1;
  if (e.gemini_description) bumpGeminiReachback();
  renderEvent(e);
  updateSectorStatus(sector, 'event');
  updateOverlay(sector, e);
  pingTopology(sector);
  reflectQueueCount();
}

// Gemini reachback counter — rolling 60s window. Pulses the metric pill
// briefly when a new Gemini call lands so judges see the cloud's per-event
// participation, not a static metric.
function bumpGeminiReachback() {
  geminiCallTimestamps.push(Date.now());
  refreshGeminiPill(true);
}

function refreshGeminiPill(flash = false) {
  const cutoff = Date.now() - 60000;
  while (geminiCallTimestamps.length && geminiCallTimestamps[0] < cutoff) {
    geminiCallTimestamps.shift();
  }
  const valueEl = document.getElementById('metric-gemini');
  if (valueEl) valueEl.textContent = String(geminiCallTimestamps.length);
  // Mirror to ARCH Gemini branch counter
  setText('arch-counter-gemini-flow', `${geminiCallTimestamps.length} /min`);
  if (flash) {
    const pill = document.getElementById('metric-gemini-pill');
    if (pill) {
      pill.classList.remove('bumped');
      void pill.offsetWidth; // restart animation
      pill.classList.add('bumped');
      setTimeout(() => pill.classList.remove('bumped'), 400);
    }
  }
}

// Drift the rolling window even when no new events arrive, so an old call
// rolls off after 60s on its own.
setInterval(() => refreshGeminiPill(false), 1000);

// De-dup events: same sector + same ts + same yolo signature should only render once.
// (Backfill on WS reconnect can otherwise re-emit events that the live stream
//  also delivers, producing the "person 87% · person 87%" double-render bug.)
const _seenEvents = new Map();  // node -> Set of dedup keys
function _dedupKey(e) {
  const labels = (e.yolo_hits || []).map((h) => `${h.label}:${(h.confidence || 0).toFixed(2)}`).join('|');
  return `${e.ts || 0}:${labels}`;
}

function renderEvent(e) {
  const container = document.getElementById(`events-${e.node}`);
  if (!container) return;

  let seen = _seenEvents.get(e.node);
  if (!seen) { seen = new Set(); _seenEvents.set(e.node, seen); }
  const key = _dedupKey(e);
  if (seen.has(key)) return;  // already rendered, skip
  seen.add(key);
  // Cap memory — keep only last 50 keys per node
  if (seen.size > 50) {
    const first = seen.values().next().value;
    seen.delete(first);
  }

  const root = document.createElement('div');
  root.className = 'event' + (e.queued_offline ? ' queued' : '');

  const row1 = document.createElement('div');
  row1.className = 'event-row1';

  const yolo = document.createElement('span');
  yolo.className = 'yolo';
  const labels = (e.yolo_hits || [])
    .map((h) => `${h.label} ${(h.confidence * 100).toFixed(0)}%`)
    .join(' · ') || '(none)';
  yolo.textContent = labels;
  row1.appendChild(yolo);

  const ts = document.createElement('span');
  ts.className = 'ts';
  ts.textContent = new Date(e.ts * 1000).toLocaleTimeString() + (e.queued_offline ? ' · replayed' : '');
  row1.appendChild(ts);
  root.appendChild(row1);

  const desc = document.createElement('div');
  if (e.gemini_description) {
    desc.className = 'event-desc';
    desc.textContent = `"${e.gemini_description}"`;
  } else {
    desc.className = 'event-desc local';
    desc.textContent = 'cloud analyst unavailable · local-only';
  }
  root.appendChild(desc);

  if (e.signature) {
    const sig = document.createElement('div');
    sig.className = 'event-sig';
    sig.textContent = e.signature;
    root.appendChild(sig);
  }

  container.insertBefore(root, container.firstChild);
  while (container.children.length > 2) container.lastChild.remove();
}

function updateOverlay(sector, e) {
  const labelEl = document.getElementById(`overlay-${sector === 'sensor-north' ? 'north' : 'south'}-label`);
  const tsEl = document.getElementById(`overlay-${sector === 'sensor-north' ? 'north' : 'south'}-ts`);
  if (!labelEl || !tsEl) return;
  const labels = (e.yolo_hits || []).map((h) => h.label).join(', ');
  labelEl.textContent = labels || 'awaiting motion';
  tsEl.textContent = new Date(e.ts * 1000).toLocaleTimeString();
  lastEventLabel[sector] = labels;
}

// Per-sector tracking so updateSectorStatus can decide final state from
// multiple inputs (job state from /jobs + event freshness from WS).
const sensorJobRunning = { 'sensor-north': null, 'sensor-south': null };

function updateSectorStatus(node, signal) {
  // Three signals can drive sector status; priority order matters because
  // they arrive on independent timers:
  //   1. Job NOT running (from /jobs poll) → "stopped"  (Beat 0 lights-up)
  //   2. Recent event arrived (from WS handleEvent)     → "live"
  //   3. No event in last 8s (from heartbeat checker)   → "offline"
  //   4. Boot, nothing observed yet                     → "connecting"
  // Callers pass the signal that just changed; we compute the final state.
  const el = document.getElementById(`status-${node}`);
  if (!el) return;

  const jobRunning = sensorJobRunning[node];

  let status;
  if (jobRunning === false) {
    // Highest priority: if the cluster says the sensor's job is stopped,
    // nothing else matters — even a stale "live" signal would be wrong.
    status = 'stopped';
  } else if (signal === 'live' || (signal === 'event' && jobRunning !== false)) {
    status = 'live';
  } else if (signal === 'offline' && jobRunning !== false) {
    status = 'offline';
  } else if (jobRunning === true) {
    // Job running but no event signal yet → leave existing state unless
    // we're at boot, in which case start at 'connecting'.
    status = el.textContent === '' ? 'connecting' : el.textContent;
  } else {
    // Job state unknown (haven't polled /jobs yet) → connecting.
    status = 'connecting';
  }

  el.textContent = status;
  el.className = 'sector-status ' + status;
}

// ── Fusion tile (replaces the old full-screen overlay) ─────────────
// Always visible. STANDBY when no recent multi-sector correlation; ACTIVE
// for 6s when one fires (LED + border light up, detail line fills with
// the contact summary), then collapses back to STANDBY with `last:` ts.

let fusionLastTs = 0;
let fusionActiveUntil = 0;

function enqueueFused(f) {
  // No queue any more — tiles can't queue, so we just take the latest.
  // Multiple rapid fusions still each light up; the most recent wins.
  renderFusionTile(f);
  pingTopology('sensor-north', '#ffa726');
  pingTopology('sensor-south', '#ffa726');
}

function renderFusionTile(f) {
  const tile = document.getElementById('fusion-tile');
  const stateEl = document.getElementById('fusion-state');
  const detailEl = document.getElementById('fusion-detail');
  const metaEl = document.getElementById('fusion-meta');
  if (!tile || !stateEl || !detailEl || !metaEl) return;

  // Build a compact one-line summary: "NORTH+SOUTH · person+backpack / cell phone"
  const sectors = (f.sectors || []).map((s) => s.replace('sensor-', '').toUpperCase()).join('+');
  const contactSummary = (f.contacts || [])
    .map((c) => (c.yolo_hits || []).join('+') || '(none)')
    .join(' / ');
  const detail = sectors ? `${sectors} · ${contactSummary}` : contactSummary;

  stateEl.textContent = 'ACTIVE';
  detailEl.textContent = detail;
  fusionLastTs = (f && f.ts) ? f.ts : (Date.now() / 1000);
  metaEl.textContent = `last: ${fmtIsoUtc(fusionLastTs)}`;

  // Restart the active class so the pulse animation runs cleanly each fire
  tile.classList.remove('active');
  void tile.offsetWidth; // restart animation
  tile.classList.add('active');

  fusionActiveUntil = Date.now() + 6000;
}

// Drive the STANDBY decay: every second, if we're past the active window,
// collapse the tile back to standby and update the "last:" age.
setInterval(() => {
  const tile = document.getElementById('fusion-tile');
  const stateEl = document.getElementById('fusion-state');
  const detailEl = document.getElementById('fusion-detail');
  const metaEl = document.getElementById('fusion-meta');
  if (!tile) return;
  if (Date.now() >= fusionActiveUntil && tile.classList.contains('active')) {
    tile.classList.remove('active');
    stateEl.textContent = 'STANDBY';
    detailEl.textContent = 'no multi-sector correlation';
  }
  // Always refresh the "last:" time display when we have a fusion history
  if (fusionLastTs > 0 && metaEl) {
    metaEl.textContent = `last: ${fmtIsoUtc(fusionLastTs)}`;
  }
}, 1000);

// ISO 8601 UTC format helper — `2026-05-02T11:48:14Z`. Per the design spec
// (refine-dashboard-design-vocabulary), the dashboard uses ISO 8601 UTC for
// all absolute timestamps.
function fmtIsoUtc(epochSeconds) {
  if (!epochSeconds || !isFinite(epochSeconds)) return '—';
  const d = new Date(epochSeconds * 1000);
  return d.toISOString().slice(0, 19) + 'Z';
}

// ── Triggers ────────────────────────────────────────────────────────

function renderTriggers(list) {
  const container = document.getElementById('trigger-chips');
  const previous = new Set(knownTriggers);
  knownTriggers.clear();
  (list || []).forEach((t) => knownTriggers.add(t));

  container.replaceChildren();
  for (const t of (list || [])) {
    const chip = document.createElement('span');
    chip.className = 'chip';
    if (!firstTriggerLoad && !previous.has(t)) {
      chip.classList.add('added');
    }
    chip.textContent = t;
    container.appendChild(chip);
  }
  firstTriggerLoad = false;
}

// ── Cloud state ─────────────────────────────────────────────────────

function setCloudState(up) {
  cloudUp = up;
  const banner = document.getElementById('cloud-banner');
  const pill = document.getElementById('cloud-pill');
  if (up) {
    banner.classList.remove('show');
    document.body.classList.remove('cloud-down');
    pill.textContent = 'CLOUD LINK · UP';
    pill.classList.remove('down');
    queuedOfflineCount = 0; // cleared on reconnect
    reflectQueueCount();
  } else {
    banner.classList.add('show');
    document.body.classList.add('cloud-down');
    pill.textContent = 'CLOUD LINK · DOWN';
    pill.classList.add('down');
  }
}

function reflectQueueCount() {
  const q = document.getElementById('queue-count');
  if (q) q.textContent = String(queuedOfflineCount);
}

// ── Jobs panel ──────────────────────────────────────────────────────

function renderJobs(list) {
  const container = document.getElementById('platform-jobs');
  container.replaceChildren();
  let running = 0, total = 0, failed = 0;
  let anySensorRunning = false;
  for (const j of (list || [])) {
    total += 1;
    // Case-insensitive status match — the fusion-node's WS payload sometimes
    // sends mixed-case status strings ("Running" vs "running") depending on
    // whether it came from the synthetic fallback or `expanso-cli job list`.
    const status = String(j.status || '').toLowerCase();
    if (status === 'running') running += 1;
    if (status === 'failed') failed += 1;
    if (status === 'running' && /^sensor-/.test(j.name)) anySensorRunning = true;

    // Inline format for the bottom strip: ■ name STATUS
    const item = document.createElement('span');
    item.className = 'job-inline ' + (status || 'pending');
    item.title = `${j.name} · ${status || 'pending'}` + (j.role ? ` · ${j.role}` : '');
    const led = document.createElement('span');
    led.className = 'job-inline-led';
    item.appendChild(led);
    const name = document.createElement('span');
    name.className = 'job-inline-name';
    name.textContent = j.name.replace(/^armyx-tech-/, '').replace(/^edge-/, '');
    item.appendChild(name);
    const statusEl = document.createElement('span');
    statusEl.className = 'job-inline-status';
    statusEl.textContent = (status || 'pending').toUpperCase();
    item.appendChild(statusEl);
    container.appendChild(item);
  }
  document.getElementById('footer-jobs').textContent = `${running}/${total}` + (failed ? ` (${failed} failed)` : '');
  // Mirror to ARCH control-plane counter
  setText('arch-counter-jobs', `${running}/${total} jobs`);

  // Tier strip: dim EDGE when no sensor-* job is in 'running' state.
  // (FUSION dimming is implicit — if the fusion node is down, this whole
  //  page isn't rendering. CLOUD dimming is driven by F1 / cloud-down.)
  document.body.classList.toggle('tier-edge-down', !anySensorRunning);

  // Per-sector job state for sector-tile badge ("stopped" vs "live"/"offline"
  // vs "connecting"). Update sensorJobRunning then recompute each badge so
  // Beat 0 lights-up shows "stopped" badges, not stale "connecting".
  for (const sectorName of Object.keys(sensorJobRunning)) {
    const job = (list || []).find((j) => j.name === sectorName);
    sensorJobRunning[sectorName] =
      job ? String(job.status || '').toLowerCase() === 'running' : null;
    updateSectorStatus(sectorName, 'jobs');
  }
}

// ── Metrics ─────────────────────────────────────────────────────────

function renderMetrics(m) {
  if (!m) return;
  document.getElementById('metric-epm').textContent = Math.round(m.events_per_minute || 0);
  document.getElementById('metric-fused').textContent = String(m.fused_alerts || 0);
  document.getElementById('footer-events').textContent = String(m.total_events || 0);
  document.getElementById('footer-signed').textContent = String(m.signed_events || 0);
  document.getElementById('footer-fused').textContent = String(m.fused_alerts || 0);
  // Mirror to ARCH view counters — Camera → Jetson stem shows the input rate.
  setText('arch-counter-rtsp', `${Math.round(m.events_per_minute || 0)} ev/min`);
  // Local Decision branch shows the rate of fused-class outputs (proxy: total fused).
  // We don't have a fused/min metric, so derive a 60s rolling rate client-side.
  fusedRollingPush(m.fused_alerts || 0);
  setText('arch-counter-local', `${fusedPerMinute()} fused/min`);
}

// Rolling 60s window to derive fused/min from the cumulative metric.
const fusedHistory = []; // { t: ms, total: number }
function fusedRollingPush(totalFused) {
  const now = Date.now();
  fusedHistory.push({ t: now, total: totalFused });
  // Trim to last 65s
  while (fusedHistory.length > 1 && now - fusedHistory[0].t > 65000) {
    fusedHistory.shift();
  }
}
function fusedPerMinute() {
  if (fusedHistory.length < 2) return 0;
  const a = fusedHistory[0];
  const b = fusedHistory[fusedHistory.length - 1];
  const dt = (b.t - a.t) / 1000;
  if (dt <= 0) return 0;
  return Math.max(0, Math.round((b.total - a.total) * (60 / dt)));
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

// Poll metrics + jobs + S3 every 2s
setInterval(async () => {
  try {
    const [m, j, s] = await Promise.all([
      fetch('/metrics').then((r) => r.json()),
      fetch('/jobs').then((r) => r.json()),
      fetch('/s3').then((r) => r.json()),
    ]);
    renderMetrics(m);
    renderJobs(j.jobs || []);
    renderS3(s);
  } catch (e) { /* offline; ignore */ }
}, 2000);

// Sectors offline if no event in 8 seconds
setInterval(() => {
  const now = Date.now();
  for (const node of Object.keys(lastSeen)) {
    if (lastSeen[node] && now - lastSeen[node] > 8000) updateSectorStatus(node, 'offline');
  }
}, 1000);

// Camera feed: preload next frame in a hidden Image, only swap once decoded.
// Without this, setting img.src directly causes a brief blank/flash during
// fetch+decode that reads as "jumpy" even though the dimensions are stable.
setInterval(() => {
  const t = Date.now();
  for (const node of ['sensor-north', 'sensor-south']) {
    const img = document.getElementById(`feed-${node}`);
    if (!img) continue;
    const next = new Image();
    next.onload = () => { img.src = next.src; };
    next.src = `/snapshot/${node}?t=${t}`;
  }
}, 2000);

// ── Cloud egress / S3 archive tile ──────────────────────────────────

let s3LastCount = 0;

function fmtAge(seconds) {
  if (seconds == null || !isFinite(seconds)) return '—';
  if (seconds < 1) return 'just now';
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

function renderS3(state) {
  if (!state) return;
  const stateEl = document.getElementById('egress-state');
  const countEl = document.getElementById('egress-count');
  const countWrap = countEl && countEl.parentElement;
  const bucketEl = document.getElementById('egress-bucket');
  const lastEl = document.getElementById('egress-last');
  const recentEl = document.getElementById('egress-recent');

  if (!state.enabled) {
    stateEl.textContent = 'standby · S3 unset';
    stateEl.className = 'egress-state offline';
    bucketEl.textContent = state.last_error || 'add bucket via .env or expanso job';
    setText('arch-counter-s3-flow', 'standby');
    return;
  }

  bucketEl.textContent = `bucket: ${state.bucket}`;
  countEl.textContent = String(state.object_count);
  setText('arch-counter-s3-flow', `${state.object_count} obj`);

  // Pulse the count when new objects arrive — the literal "data is moving" tell.
  if (state.object_count > s3LastCount && countWrap) {
    countWrap.classList.remove('bumped');
    void countWrap.offsetWidth; // force reflow so the animation restarts
    countWrap.classList.add('bumped');
  }
  s3LastCount = state.object_count;

  if (state.last_poll_ok === false) {
    stateEl.textContent = 'poll error';
    stateEl.className = 'egress-state error';
    lastEl.textContent = state.last_error ? `err: ${state.last_error.slice(0, 60)}` : 'last upload: —';
  } else if (state.last_upload_ts == null) {
    stateEl.textContent = 'awaiting first upload';
    stateEl.className = 'egress-state offline';
    lastEl.textContent = 'last upload: —';
  } else if (state.stalled) {
    stateEl.textContent = 'stalled · queued at edge';
    stateEl.className = 'egress-state stalled';
    lastEl.textContent = `last upload: ${fmtAge(state.seconds_since_upload)}`;
  } else {
    stateEl.textContent = 'live';
    stateEl.className = 'egress-state live';
    lastEl.textContent = `last upload: ${fmtAge(state.seconds_since_upload)}`;
  }

  recentEl.replaceChildren();
  for (const obj of (state.recent_keys || [])) {
    const li = document.createElement('li');
    li.dataset.key = obj.key;
    const k = document.createElement('span');
    k.className = 'key';
    k.textContent = obj.key;
    li.appendChild(k);
    const t = document.createElement('span');
    t.className = 'ts';
    t.textContent = fmtAge((Date.now() / 1000) - obj.last_modified);
    li.appendChild(t);
    li.addEventListener('click', () => openS3Modal(obj.key));
    recentEl.appendChild(li);
  }
}

async function openS3Modal(key) {
  const modal = document.getElementById('s3-modal');
  const title = document.getElementById('s3-modal-title');
  const body = document.getElementById('s3-modal-body');
  title.textContent = key;
  body.textContent = 'loading…';
  modal.classList.add('show');
  try {
    const r = await fetch(`/s3/object?key=${encodeURIComponent(key)}`);
    const data = await r.json();
    if (data.error) {
      body.textContent = `error: ${data.error}`;
      return;
    }
    // Pretty-print JSON if the body parses; otherwise show raw
    try {
      body.textContent = JSON.stringify(JSON.parse(data.body), null, 2);
    } catch (e) {
      body.textContent = data.body;
    }
    if (data.truncated) body.textContent += '\n\n… (truncated)';
  } catch (e) {
    body.textContent = `fetch failed: ${e}`;
  }
}

document.getElementById('s3-modal-close').addEventListener('click', () => {
  document.getElementById('s3-modal').classList.remove('show');
});
document.getElementById('s3-modal').addEventListener('click', (ev) => {
  // Click on the backdrop (not the card) closes the modal.
  if (ev.target && ev.target.id === 's3-modal') {
    ev.currentTarget.classList.remove('show');
  }
});
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape') {
    document.getElementById('s3-modal').classList.remove('show');
  }
});

// ── Topology mini canvas ────────────────────────────────────────────

const NODES = {
  'sensor-north':  { x: 50,  y: 40,  label: 'N' },
  'sensor-south':  { x: 50,  y: 120, label: 'S' },
  'fusion-node':   { x: 230, y: 80,  label: 'F' },
};

function pingTopology(sensor, color = '#3ddc84') {
  topoArrows.push({ from: sensor, to: 'fusion-node', color, ttl: 1.0 });
}

function drawTopology() {
  topoCtx.clearRect(0, 0, topo.width, topo.height);

  // Edges (static guide)
  topoCtx.strokeStyle = '#262b33';
  topoCtx.lineWidth = 2;
  for (const sensor of ['sensor-north', 'sensor-south']) {
    const a = NODES[sensor], b = NODES['fusion-node'];
    topoCtx.beginPath(); topoCtx.moveTo(a.x, a.y); topoCtx.lineTo(b.x, b.y); topoCtx.stroke();
  }

  // Animated arrows
  for (const arr of topoArrows) {
    const a = NODES[arr.from], b = NODES[arr.to];
    if (!a || !b) continue;
    const t = 1 - arr.ttl; // 0 → 1 progress
    const px = a.x + (b.x - a.x) * t;
    const py = a.y + (b.y - a.y) * t;
    topoCtx.fillStyle = arr.color;
    topoCtx.globalAlpha = arr.ttl;
    topoCtx.beginPath(); topoCtx.arc(px, py, 6, 0, Math.PI * 2); topoCtx.fill();
    topoCtx.globalAlpha = 1;
    arr.ttl -= 0.04;
  }
  for (let i = topoArrows.length - 1; i >= 0; i--) if (topoArrows[i].ttl <= 0) topoArrows.splice(i, 1);

  // Nodes
  for (const [name, n] of Object.entries(NODES)) {
    topoCtx.fillStyle = name === 'fusion-node' ? '#7fb8dc' : '#3ddc84';
    topoCtx.beginPath(); topoCtx.arc(n.x, n.y, 14, 0, Math.PI * 2); topoCtx.fill();
    topoCtx.fillStyle = '#0b0d10';
    topoCtx.font = 'bold 14px ui-monospace, Menlo, monospace';
    topoCtx.textAlign = 'center';
    topoCtx.textBaseline = 'middle';
    topoCtx.fillText(n.label, n.x, n.y);
  }
}
setInterval(drawTopology, 100);

// ── Tab routing (URL-hash based, bookmarkable, opens in new tabs) ──
// `#ops` → OPS view, `#arch` → ARCH view, `#archive` → ARCHIVE view (S3 tile).
// Tabs are anchor elements with real hrefs, so cmd/middle-click opens a fresh
// browser tab pointed straight at the chosen view.

const TABS = ['ops', 'arch', 'archive'];

function applyTabFromHash() {
  const raw = (window.location.hash || '#ops').slice(1).toLowerCase();
  const target = TABS.includes(raw) ? raw : 'ops';
  document.body.classList.remove('tab-ops', 'tab-arch', 'tab-archive');
  document.body.classList.add(`tab-${target}`);
  for (const a of document.querySelectorAll('.tab-switcher .tab')) {
    const isActive = a.dataset.tab === target;
    a.classList.toggle('is-active', isActive);
    a.setAttribute('aria-selected', isActive ? 'true' : 'false');
  }
}

window.addEventListener('hashchange', applyTabFromHash);
applyTabFromHash();

// ── Operator keyboard shortcuts ─────────────────────────────────────

document.addEventListener('keydown', async (ev) => {
  if (ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA')) return;
  switch (ev.key) {
    case 'F1': ev.preventDefault(); await fetch('/demo/wan-down', { method: 'POST' }); break;
    case 'F2': ev.preventDefault(); await fetch('/demo/wan-up',   { method: 'POST' }); break;
    case 'F3': ev.preventDefault(); await fetch('/demo/fused-test', { method: 'POST' }); break;
    case 'F4': ev.preventDefault();
      await fetch('/triggers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ triggers: ['person', 'backpack', 'drone', 'airplane', 'car', 'truck'] }),
      });
      break;
  }
});
