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
const alertQueue = [];
let alertShowing = false;

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
    case 'alert':    enqueueAlert(m.data); break;
    // Back-compat: legacy WS event name from before the rename. Keep
    // listening for it so a stale orchestrator + new dashboard still
    // surfaces alerts during a partial roll-out.
    case 'fused':    enqueueAlert(m.data); break;
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
  // Mirror to ARCH Gemini branch counter + particle-flow rate
  setText('arch-counter-gemini-flow', `${geminiCallTimestamps.length} /min`);
  setFlowRate('arch-flow-gemini', geminiCallTimestamps.length);
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

  const hits = e.yolo_hits || [];
  const isEmpty = hits.length === 0;
  // Real Gemini call (not the canned/local fallback) is signalled by the
  // sensor populating model_versions.gemini. The dashboard shows a pill
  // on those events so the audience sees which detections the cloud
  // analyst actually weighed in on.
  const usedGemini = !!(e.model_versions && e.model_versions.gemini);

  const root = document.createElement('div');
  root.className =
    'event' +
    (e.queued_offline ? ' queued' : '') +
    (isEmpty ? ' empty' : '') +
    (usedGemini ? ' gemini-augmented' : '');

  const row1 = document.createElement('div');
  row1.className = 'event-row1';

  const yolo = document.createElement('span');
  yolo.className = 'yolo' + (isEmpty ? ' empty' : '');
  if (isEmpty) {
    yolo.textContent = 'empty';
  } else {
    // Each label gets its own span so we can color them per class
    // (person = cyan, backpack = amber, drone = red). Confidence
    // floats inline next to the label.
    hits.forEach((h, i) => {
      if (i > 0) {
        const sep = document.createElement('span');
        sep.className = 'yolo-sep';
        sep.textContent = ' · ';
        yolo.appendChild(sep);
      }
      const display = displayLabel(h.label);
      const cls = String(display).toLowerCase();
      const span = document.createElement('span');
      span.className = `yolo-label yolo-label--${cls}`;
      span.textContent = `${display} ${(h.confidence * 100).toFixed(0)}%`;
      yolo.appendChild(span);
    });
  }
  row1.appendChild(yolo);

  // Gemini-Augmented pill — sits inline next to the labels so it's
  // visually adjacent to the actual classification info.
  if (usedGemini) {
    const pill = document.createElement('span');
    pill.className = 'gemini-pill';
    pill.textContent = 'Gemini Augmented';
    row1.appendChild(pill);
  }

  const ts = document.createElement('span');
  ts.className = 'ts';
  ts.textContent = new Date(e.ts * 1000).toLocaleTimeString() + (e.queued_offline ? ' · replayed' : '');
  row1.appendChild(ts);
  root.appendChild(row1);

  // Empty events skip the description row entirely — no point describing
  // a frame that has nothing to describe.
  if (!isEmpty) {
    const desc = document.createElement('div');
    if (e.gemini_description) {
      desc.className = 'event-desc';
      desc.textContent = `"${e.gemini_description}"`;
    } else {
      desc.className = 'event-desc local';
      desc.textContent = 'cloud analyst unavailable · local-only';
    }
    root.appendChild(desc);
  }

  if (e.signature) {
    const sig = document.createElement('div');
    sig.className = 'event-sig';
    sig.textContent = e.signature;
    root.appendChild(sig);
  }

  container.insertBefore(root, container.firstChild);
  // Show up to 8 single-line events per panel — fills the column instead of leaving 70% empty.
  while (container.children.length > 8) container.lastChild.remove();
}

function updateOverlay(sector, e) {
  const labelEl = document.getElementById(`overlay-${sector === 'sensor-north' ? 'north' : 'south'}-label`);
  const tsEl = document.getElementById(`overlay-${sector === 'sensor-north' ? 'north' : 'south'}-ts`);
  if (!labelEl || !tsEl) return;
  const labels = (e.yolo_hits || []).map((h) => displayLabel(h.label)).join(", ");
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

let alertLastTs = 0;
let alertActiveUntil = 0;

function enqueueAlert(f) {
  // No queue any more — tiles can't queue, so we just take the latest.
  // Multiple rapid fusions still each light up; the most recent wins.
  renderFusionTile(f);
  pingTopology('sensor-north', '#ffa726');
  pingTopology('sensor-south', '#ffa726');
}

// Human-readable label for each correlator rule. Falls back to the raw
// rule string for any new rule wired up server-side without a label here.
const ALERT_RULE_LABELS = {
  backpack_detected:    'BACKPACK DETECTED',
  multiple_persons:     'MULTIPLE PERSONS',
  drone_after_update:   'DRONE DETECTED',
  synthetic:            'SYNTHETIC (REHEARSAL)',
  // Legacy keys kept so a degraded rolling deploy where the orchestrator
  // hasn't been respawned yet doesn't display the raw snake_case string.
  person_with_backpack: 'BACKPACK DETECTED',
  person_cross_sector:  'MULTIPLE PERSONS',
};

function renderFusionTile(f) {
  const tile = document.getElementById('alert-tile');
  const stateEl = document.getElementById('alert-state');
  const detailEl = document.getElementById('alert-detail');
  const metaEl = document.getElementById('alert-meta');
  if (!tile || !stateEl || !detailEl || !metaEl) return;

  // Build a compact one-line summary like:
  //   "PERSON + BACKPACK — NORTH · person+backpack"
  //   "PERSON · BOTH SECTORS — NORTH+SOUTH · person / person"
  // The rule label leads so the audience reads the trigger before the
  // raw class list.
  const sectors = (f.sectors || []).map((s) => s.replace('sensor-', '').toUpperCase()).join('+');
  const contactSummary = (f.contacts || [])
    .map((c) => (c.yolo_hits || []).join('+') || '(none)')
    .join(' / ');
  const ruleLabel = ALERT_RULE_LABELS[f.rule] || (f.rule || 'ALERT').toUpperCase();
  const detail = sectors
    ? `${ruleLabel} — ${sectors} · ${contactSummary}`
    : `${ruleLabel} — ${contactSummary}`;

  stateEl.textContent = 'ACTIVE';
  detailEl.textContent = detail;
  alertLastTs = (f && f.ts) ? f.ts : (Date.now() / 1000);
  metaEl.textContent = `last: ${fmtIsoUtc(alertLastTs)}`;

  // Restart the active class so the pulse animation runs cleanly each fire
  tile.classList.remove('active');
  void tile.offsetWidth; // restart animation
  tile.classList.add('active');

  alertActiveUntil = Date.now() + 6000;
}

// Drive the STANDBY decay: every second, if we're past the active window,
// collapse the tile back to standby and update the "last:" age.
setInterval(() => {
  const tile = document.getElementById('alert-tile');
  const stateEl = document.getElementById('alert-state');
  const detailEl = document.getElementById('alert-detail');
  const metaEl = document.getElementById('alert-meta');
  if (!tile) return;
  if (Date.now() >= alertActiveUntil && tile.classList.contains('active')) {
    tile.classList.remove('active');
    stateEl.textContent = 'STANDBY';
    detailEl.textContent = 'no alerts';
  }
  // Always refresh the "last:" time display when we have a fusion history
  if (alertLastTs > 0 && metaEl) {
    metaEl.textContent = `last: ${fmtIsoUtc(alertLastTs)}`;
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
    const display = displayLabel(t);
    chip.className = `chip chip--${String(display).toLowerCase()}`;
    if (!firstTriggerLoad && !previous.has(t)) {
      chip.classList.add('added');
    }
    chip.textContent = display;
    container.appendChild(chip);
  }

  // Big-signal moment: the operator just rolled out drone detection.
  // Skip on first load (initial WS payload would otherwise fire the
  // overlay every page refresh).
  if (!firstTriggerLoad && !previous.has('drone') && knownTriggers.has('drone')) {
    showPipelineUpdateOverlay('drone');
  }

  firstTriggerLoad = false;
}

// Full-viewport "PIPELINE UPDATED" takeover. Fires the moment the active
// trigger set transitions to include `drone` (or, generally, any class
// passed in). Pulses for ~6s, then collapses back. Audience sees: the
// operator rolled out the new class, the cluster picked it up, sensors
// will now flag it.
function showPipelineUpdateOverlay(newClass) {
  const overlay = document.getElementById('pipeline-update-overlay');
  if (!overlay) return;
  const subjectEl = overlay.querySelector('.pipeline-update-class');
  if (subjectEl) subjectEl.textContent = displayLabel(newClass).toUpperCase();
  overlay.classList.remove('show');
  void overlay.offsetWidth; // restart entry animation
  overlay.classList.add('show');
  clearTimeout(showPipelineUpdateOverlay._timer);
  showPipelineUpdateOverlay._timer = setTimeout(() => {
    overlay.classList.remove('show');
  }, 6000);
}

// COCO doesn't have a "drone" class — YOLO classifies drones as "airplane".
// Alias the display so the demo narrative reads correctly without needing
// custom YOLO weights. Backend still uses "airplane" for matching.
const _LABEL_ALIASES = { airplane: 'drone' };
function displayLabel(label) {
  return _LABEL_ALIASES[String(label).toLowerCase()] || label;
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
  // Mirror to ARCH control-plane counter + particle-flow rate. Job-state
  // changes are rare (often 4/4 steady), so derive the rate from the running
  // count itself: more running jobs → control-plane is more "active".
  setText('arch-counter-jobs', `${running}/${total} jobs`);
  jobsRollingPush(running);
  setFlowRate('arch-flow-jobs', jobsChangePerMinute());

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
  const epm = Math.round(m.events_per_minute || 0);
  document.getElementById('metric-epm').textContent = epm;
  document.getElementById('metric-fused').textContent = String(m.fused_alerts || 0);
  document.getElementById('footer-events').textContent = String(m.total_events || 0);
  document.getElementById('footer-signed').textContent = String(m.signed_events || 0);
  document.getElementById('footer-fused').textContent = String(m.fused_alerts || 0);
  // Mirror to ARCH view counters — Camera → Jetson stem shows the input rate.
  setText('arch-counter-rtsp', `${epm} ev/min`);
  setFlowRate('arch-flow-rtsp', epm);
  // Local Decision branch shows the rate of fused-class outputs (proxy: total fused).
  // We don't have a fused/min metric, so derive a 60s rolling rate client-side.
  fusedRollingPush(m.fused_alerts || 0);
  const fpm = fusedPerMinute();
  setText('arch-counter-local', `${fpm} fused/min`);
  setFlowRate('arch-flow-local', fpm);
  // Live-stat block on the Jetson box: real numbers describing the node.
  setText('arch-jet-epm', String(epm));
  setText('arch-jet-fps', archFpsString(epm));
  setText('arch-jet-age', archLastFrameAgeString(epm));
  setText('arch-jet-queued', String(m.queued_offline || 0));
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

// ── ARCH particle-flow speed control ─────────────────────────────────
// Map a per-minute rate to an animation-duration so faster rates → faster
// dots. Capped so the dots are always visible (not too slow, not so fast
// they smear). 0 → 6s (very slow standby), >=120/min → 0.4s (firehose).
function rateToDurationSec(perMin) {
  const r = Math.max(0, Number(perMin) || 0);
  if (r <= 0) return 6.0;
  // Linear-ish map between 1/min (3s) and 120/min (0.4s).
  const minDur = 0.4, maxDur = 3.0, peak = 120;
  const t = Math.min(1, r / peak);
  return Math.max(minDur, maxDur - (maxDur - minDur) * t);
}
function setFlowRate(elementId, perMin) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const dur = rateToDurationSec(perMin).toFixed(2);
  el.style.animationDuration = `${dur}s`;
}

// Rolling window for the cloud-control-plane particle: jobs-running count
// changes are rare, so we treat absolute changes as activity.
const jobsHistory = []; // { t: ms, running: number }
let jobsLastRunning = -1;
let jobsChangeStamps = []; // ms timestamps of state changes in the last 60s
function jobsRollingPush(running) {
  const now = Date.now();
  if (jobsLastRunning !== -1 && jobsLastRunning !== running) {
    jobsChangeStamps.push(now);
  }
  jobsLastRunning = running;
  jobsChangeStamps = jobsChangeStamps.filter((t) => now - t < 60000);
  jobsHistory.push({ t: now, running });
  while (jobsHistory.length > 1 && now - jobsHistory[0].t > 65000) {
    jobsHistory.shift();
  }
}
function jobsChangePerMinute() {
  // Floor at 6/min so the cloud→jetson dot always has a visible heartbeat,
  // since job state usually sits steady at 4/4.
  return Math.max(6, jobsChangeStamps.length);
}

// Rolling window for the S3 particle: derive object-count delta per minute.
const s3History = []; // { t: ms, count: number }
function s3RollingPush(count) {
  const now = Date.now();
  s3History.push({ t: now, count });
  while (s3History.length > 1 && now - s3History[0].t > 65000) {
    s3History.shift();
  }
}
function s3DeltaPerMinute() {
  if (s3History.length < 2) return 0;
  const a = s3History[0];
  const b = s3History[s3History.length - 1];
  const dt = (b.t - a.t) / 1000;
  if (dt <= 0) return 0;
  return Math.max(0, Math.round((b.count - a.count) * (60 / dt)));
}

// ── ARCH Jetson live-stat helpers ────────────────────────────────────
// frames/sec: count MJPEG frame transitions on the two camera feeds in
// the last 5s. The <img src="/stream/..."> elements emit `load` each time
// a fresh JPEG arrives, so we tally those and divide.
const _archFrameStamps = []; // ms timestamps
function _onMjpegFrame() { _archFrameStamps.push(Date.now()); }
for (const id of ['feed-sensor-north', 'feed-sensor-south']) {
  const img = document.getElementById(id);
  if (img) img.addEventListener('load', _onMjpegFrame);
}
function archFpsString(eventsPerMin) {
  const now = Date.now();
  while (_archFrameStamps.length && now - _archFrameStamps[0] > 5000) {
    _archFrameStamps.shift();
  }
  const fps = _archFrameStamps.length / 5;
  if (fps > 0) return fps.toFixed(1);
  // Fallback when on ARCH tab (cameras hidden, MJPEG not loading): derive
  // a coarse fps from events/min — each event corresponds to ~1 detection
  // frame from one of two sensors, so total frames ~ epm/60 * 2 (rough).
  const proxy = (Number(eventsPerMin) || 0) / 30;
  if (proxy > 0) return `~${proxy.toFixed(1)}`;
  return '—';
}
function archLastFrameAgeString(eventsPerMin) {
  if (_archFrameStamps.length) {
    const last = _archFrameStamps[_archFrameStamps.length - 1];
    const ageS = (Date.now() - last) / 1000;
    if (ageS < 1) return '<1s';
    if (ageS < 60) return `${Math.round(ageS)}s`;
    return `${Math.round(ageS / 60)}m`;
  }
  // No MJPEG load → if events are flowing, frames are arriving on the
  // sensor side too. Show the live-events freshness as a proxy.
  if ((Number(eventsPerMin) || 0) > 0) return 'live';
  return '—';
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

// Camera feeds use the orchestrator's MJPEG /stream/{sector} endpoint —
// browser holds an HTTP connection open and renders new frames as the
// orchestrator pushes them (~10fps, baked-in YOLO bbox overlays). No JS
// polling needed; the <img src="/stream/...> in index.html does it natively.
//
// If the connection drops (sensor restart, orchestrator restart), reload
// the img by tickling its src — browser otherwise won't auto-reconnect.
setInterval(() => {
  for (const node of ['sensor-north', 'sensor-south']) {
    const img = document.getElementById(`feed-${node}`);
    if (!img) continue;
    // naturalWidth === 0 means the stream broke; reload it.
    if (img.complete && img.naturalWidth === 0) {
      img.src = `/stream/${node}?reconnect=${Date.now()}`;
    }
  }
}, 5000);

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
    setFlowRate('arch-flow-s3', 0);
    return;
  }

  bucketEl.textContent = `bucket: ${state.bucket}`;
  countEl.textContent = String(state.object_count);
  setText('arch-counter-s3-flow', `${state.object_count} obj`);
  // S3 particle-flow rate: derive object-delta/min from a rolling window of
  // /s3 polls so the dot speeds up when archive throughput climbs.
  s3RollingPush(state.object_count);
  setFlowRate('arch-flow-s3', s3DeltaPerMinute());

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
  // ARCH view curves need to retarget when the view becomes visible
  // (display: none → grid hides their geometry until tab-arch is set).
  if (target === 'arch') {
    requestAnimationFrame(() => {
      updateCameraCurves();
      updateEdgeOutCurves();
    });
  }
}

window.addEventListener('hashchange', applyTabFromHash);
applyTabFromHash();

// ── ARCH view: anchor SVG curves to live element geometry ─────────────
// SVG paths can't follow elements declaratively. Read the live geometry
// of source + destination boxes via getBoundingClientRect(), write fresh
// `d` attributes. The SVGs use pixel coordinates (no viewBox), so all
// math happens in SVG-local screen-space. Re-runs on load, resize,
// tab-switch, and arch-view ResizeObserver — anything that could shift
// the layout.

// Build a cubic Bezier path string with control points biased toward a
// horizontal flow first, then bending to the destination. This produces
// the merging-streams / fan-out look rather than a plain diagonal.
function buildCurve(sx, sy, ex, ey) {
  const cpx = sx + (ex - sx) * 0.55;
  return `M ${sx} ${sy} C ${cpx} ${sy}, ${cpx} ${ey}, ${ex} ${ey}`;
}

// Cameras (left) → Edge box left-middle (CONVERGE). 2 curves.
function updateCameraCurves() {
  const svg   = document.querySelector('.arch-cam-curves');
  const camN  = document.getElementById('arch-tile-cam-north');
  const camS  = document.getElementById('arch-tile-cam-south');
  const edge  = document.querySelector('.arch-box--jetson');
  const pathN = document.getElementById('cam-curve-north');
  const pathS = document.getElementById('cam-curve-south');
  if (!svg || !camN || !camS || !edge || !pathN || !pathS) return;
  const sR = svg.getBoundingClientRect();
  if (!sR.width || !sR.height) return;

  const nR = camN.getBoundingClientRect();
  const sR2 = camS.getBoundingClientRect();
  const eR = edge.getBoundingClientRect();
  const toX = (x) => x - sR.left, toY = (y) => y - sR.top;
  const ex = toX(eR.left), ey = toY(eR.top + eR.height / 2);
  pathN.setAttribute('d', buildCurve(toX(nR.right), toY(nR.top + nR.height / 2), ex, ey));
  pathS.setAttribute('d', buildCurve(toX(sR2.right), toY(sR2.top + sR2.height / 2), ex, ey));
}

// Edge box right-middle → 3 destinations (DIVERGE). 3 curves with
// independent particles. Each meta chip is positioned at the midpoint of
// its curve so it labels the branch without overlapping the endpoints.
function updateEdgeOutCurves() {
  const svg   = document.querySelector('.arch-edge-out-curves');
  const edge  = document.querySelector('.arch-box--jetson');
  const dL    = document.getElementById('arch-box-dest-local');
  const dG    = document.getElementById('arch-box-dest-gemini');
  const dS    = document.getElementById('arch-box-dest-s3');
  const pL    = document.getElementById('edge-curve-local');
  const pG    = document.getElementById('edge-curve-gemini');
  const pS    = document.getElementById('edge-curve-s3');
  const mL    = document.getElementById('arch-meta-local');
  const mG    = document.getElementById('arch-meta-gemini');
  const mS    = document.getElementById('arch-meta-s3');
  if (!svg || !edge || !dL || !dG || !dS || !pL || !pG || !pS) return;
  const sR = svg.getBoundingClientRect();
  if (!sR.width || !sR.height) return;

  const eR = edge.getBoundingClientRect();
  const toX = (x) => x - sR.left, toY = (y) => y - sR.top;
  // Origin: right edge of EXPANSO EDGE box, vertical center
  const ox = toX(eR.right), oy = toY(eR.top + eR.height / 2);

  for (const [destEl, pathEl, metaEl] of [[dL, pL, mL], [dG, pG, mG], [dS, pS, mS]]) {
    const dR = destEl.getBoundingClientRect();
    const ex = toX(dR.left), ey = toY(dR.top + dR.height / 2);
    pathEl.setAttribute('d', buildCurve(ox, oy, ex, ey));
    if (metaEl) {
      // Place meta chip at curve midpoint (approximated as path bbox center).
      const mx = (ox + ex) / 2;
      const my = (oy + ey) / 2;
      metaEl.style.left = `${mx}px`;
      metaEl.style.top  = `${my}px`;
    }
  }
}

function updateAllArchCurves() {
  updateCameraCurves();
  updateEdgeOutCurves();
}

window.addEventListener('resize', () => requestAnimationFrame(updateAllArchCurves));
window.addEventListener('load',   () => setTimeout(updateAllArchCurves, 100));
if (window.ResizeObserver) {
  const ro = new ResizeObserver(() => requestAnimationFrame(updateAllArchCurves));
  const av = document.querySelector('.arch-view');
  if (av) ro.observe(av);
}

// ── Operator keyboard shortcuts ─────────────────────────────────────

document.addEventListener('keydown', async (ev) => {
  if (ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA')) return;
  switch (ev.key) {
    case 'F1': ev.preventDefault(); await fetch('/demo/wan-down', { method: 'POST' }); break;
    case 'F2': ev.preventDefault(); await fetch('/demo/wan-up',   { method: 'POST' }); break;
    case 'F3': ev.preventDefault(); await fetch('/demo/fused-test', { method: 'POST' }); break;
    case 'F4': ev.preventDefault();
      // Beat 4: end state is exactly person + backpack + drone. One new chip
      // pulses in. That's the entire trigger-bar story, do not bloat.
      await fetch('/triggers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ triggers: ['person', 'backpack', 'drone'] }),
      });
      break;
  }
});

// ── WebRTC live video + bbox canvas overlay ─────────────────────────
// Replaces the snapshot polling for the camera tiles. Video comes from
// go2rtc on :1984 (separate Jetson sidecar process). YOLO bbox coords
// arrive via the existing WS event stream — drawn on a canvas overlay
// positioned over the <video>. Video runs at native ~25fps; bbox redraws
// at YOLO's ~5-10Hz inference rate. They're decoupled — video stays smooth
// even when YOLO is mid-inference.

const GO2RTC_BASE = `${location.protocol}//${location.hostname}:1984`;
// Source dims are detected per-sector from the actual <video>'s videoWidth/Height
// at draw time, NOT hardcoded — sensor's RTSP source can be sub-stream (640×360),
// main (1280×720), or 4K depending on config. Fallback used until video has loaded.
const SECTOR_SOURCE_W_FALLBACK = 640;
const SECTOR_SOURCE_H_FALLBACK = 360;
const _bboxClearTimers = {};    // sector -> setTimeout handle for clear-after-hold

async function startWebRTCFor(sectorEl) {
  const stream = sectorEl.dataset.stream;
  const sector = sectorEl.dataset.sector;
  if (!stream || !sector) return;
  const video = document.getElementById(`video-${sector}`);
  if (!video) return;

  try {
    // No STUN servers — Mac and Jetson are on the same wired LAN (192.168.2.x),
    // so HOST candidates alone are sufficient for ICE. Adding a public STUN
    // server (Google's, etc.) makes the demo dependent on Internet reachability:
    // when F1 fires (Jetson WiFi off) the Mac loses its only WAN path,
    // STUN times out, ICE fails, and the video stream breaks even though
    // the Jetson is still pumping RTP over the wired LAN.
    // (Beat 5A bug: video froze on WAN-down. Removing STUN fixes it.)
    const pc = new RTCPeerConnection({ iceServers: [] });
    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.addTransceiver('audio', { direction: 'recvonly' });
    pc.ontrack = (e) => {
      video.srcObject = e.streams[0];
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const resp = await fetch(`${GO2RTC_BASE}/api/webrtc?src=${encodeURIComponent(stream)}`, {
      method: 'POST',
      body: pc.localDescription.sdp,
    });
    if (!resp.ok) throw new Error(`go2rtc HTTP ${resp.status}`);
    const answerSdp = await resp.text();
    await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
    // Maintain a small playout buffer so the WebRTC stream lines up with the
    // YOLO bbox events. With the TRT engine on the Orin GPU, inference is
    // ~10–30ms per frame and WS transport is ~50ms — total bbox lag ~150ms.
    // We previously set this to 2.0s when YOLO ran on CPU and trailed by
    // 500ms+; with GPU we can drop it back to ~0.15s and the video is
    // nearly live with the bracket riding on top in real time.
    for (const r of pc.getReceivers()) {
      if (r.track && r.track.kind === 'video') r.playoutDelayHint = 0.15;
    }
    console.log(`[webrtc] ${sector} ← ${stream} negotiated (playoutDelay 150ms)`);
  } catch (err) {
    console.warn(`[webrtc] ${sector} failed, falling back to JPEG snapshot:`, err);
    // The fallback <img class="sector-fallback"> is z-index 0 underneath the video.
    // When video has no srcObject, it's transparent — img shows through.
    // Trigger a re-poll on the fallback img every 300ms as before.
    setInterval(() => {
      const img = document.getElementById(`feed-${sector}`);
      if (img) img.src = `/snapshot/${sector}?t=${Date.now()}`;
    }, 300);
  }
}

function _drawTrackGate(ctx, x1, y1, x2, y2, color) {
  const leg = Math.max(14, Math.min((x2 - x1) / 4, (y2 - y1) / 4, 32));
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.lineJoin = 'miter';
  // 4 corner brackets
  ctx.beginPath(); ctx.moveTo(x1, y1 + leg); ctx.lineTo(x1, y1); ctx.lineTo(x1 + leg, y1); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x2 - leg, y1); ctx.lineTo(x2, y1); ctx.lineTo(x2, y1 + leg); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x1, y2 - leg); ctx.lineTo(x1, y2); ctx.lineTo(x1 + leg, y2); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x2 - leg, y2); ctx.lineTo(x2, y2); ctx.lineTo(x2, y2 - leg); ctx.stroke();
}

function drawBboxOverlay(sector, hits) {
  const canvas = document.getElementById(`overlay-${sector}`);
  if (!canvas) return;
  // Match canvas internal dims to its display dims so 1px = 1px.
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  if (canvas.width !== cw) canvas.width = cw;
  if (canvas.height !== ch) canvas.height = ch;

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, cw, ch);
  if (!hits || !hits.length) return;

  // Replicate the video element's `object-fit: contain` transform so bbox
  // coords (in source frame space — whatever resolution YOLO inferred on)
  // project onto the visible canvas. With `contain`, the video is scaled
  // by min(cw/sw, ch/sh) and centered, leaving letterbox bars on whichever
  // axis has slack. Use Math.min (not max — that would be cover semantics
  // and put boxes outside the video onto the letterbox bars).
  const video = document.getElementById(`video-${sector}`);
  const sw = (video && video.videoWidth) || SECTOR_SOURCE_W_FALLBACK;
  const sh = (video && video.videoHeight) || SECTOR_SOURCE_H_FALLBACK;
  const scale = Math.min(cw / sw, ch / sh);
  const renderedW = sw * scale;
  const renderedH = sh * scale;
  const offsetX = (cw - renderedW) / 2;
  const offsetY = (ch - renderedH) / 2;

  ctx.font = 'bold 13px "IBM Plex Mono", ui-monospace, monospace';
  ctx.textBaseline = 'bottom';

  for (let i = 0; i < hits.length; i++) {
    const hit = hits[i];
    if (!hit || !hit.bbox) continue;
    const [x1s, y1s, x2s, y2s] = hit.bbox;
    const x1 = x1s * scale + offsetX;
    const y1 = y1s * scale + offsetY;
    const x2 = x2s * scale + offsetX;
    const y2 = y2s * scale + offsetY;
    _drawTrackGate(ctx, x1, y1, x2, y2, '#ffa726');
    const labelText = `TRK-${String(i + 1).padStart(3, '0')} ${displayLabel(hit.label).toUpperCase()} ${Math.round((hit.confidence || 0) * 100)}`;
    ctx.fillStyle = '#ffa726';
    ctx.fillText(labelText, x1, Math.max(y1 - 4, 14));
  }
}

// Hook: every WS event with yolo_hits triggers an overlay redraw. Hold ~1s.
// Tradeoff: a longer hold (e.g. 3.5s) bridges gaps between sparse events but
// leaves a stale bracket painted where a fast-moving subject WAS while they
// continue walking — looks like the tracker is drunk. 1s vanishes stale boxes
// before the YOLO/transport lag (~300–500ms) becomes visually distracting,
// while still keeping the bracket up most frames at ~2 events/sec/sector.
function pushBboxOverlay(e) {
  if (!e || !e.node || !e.yolo_hits) return;
  drawBboxOverlay(e.node, e.yolo_hits);
  if (_bboxClearTimers[e.node]) clearTimeout(_bboxClearTimers[e.node]);
  _bboxClearTimers[e.node] = setTimeout(() => {
    drawBboxOverlay(e.node, []);
  }, 1000);
}

// Boot WebRTC for every .sector-feed[data-stream] (after page load so DOM exists).
window.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.sector-feed[data-stream]').forEach((el) => {
    startWebRTCFor(el);
  });
});

// Hook the WebSocket message dispatcher to push to the bbox overlay too.
// (Reassigning `handleEvent` directly is fragile across linter reorderings.)
const _origOnMessage = ws.onmessage;
ws.onmessage = function (msg) {
  _origOnMessage.call(ws, msg);
  try {
    const m = JSON.parse(msg.data);
    if (m.type === 'event') {
      pushBboxOverlay(m.data);
    } else if (m.type === 'backfill' && Array.isArray(m.data)) {
      const recent = m.data[m.data.length - 1];
      if (recent && recent.node && recent.yolo_hits) pushBboxOverlay(recent);
    }
  } catch (_e) { /* not JSON or schema mismatch — ignore */ }
};
