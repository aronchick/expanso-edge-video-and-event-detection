// Edge ISR dashboard data layer: the orchestrator WebSocket, the polls, the
// zone tally, alerts, triggers, jobs, metrics, the S3 archive tile and the
// operator keys. All server-provided strings are rendered with textContent or
// createElement, so the dashboard is XSS-safe. Chrome (tabs, theme, ARCH
// curves) lives in ui.js; camera tiles live in feeds.js.

const lastSeen = { 'sensor-north': 0, 'sensor-south': 0 };

const lastEventLabel = { 'sensor-north': '', 'sensor-south': '' };

const lastEventTs = { 'sensor-north': 0, 'sensor-south': 0 };

const knownTriggers = new Set();

let firstTriggerLoad = true;

let cloudUp = true;

let queuedOfflineCount = 0;

// Rolling 60s window of analyst reachback timestamps (ms): every event with a
// non-empty gemini_description means the edge asked the cloud analyst.
const analystCallTimestamps = [];

// Topology canvas state (hidden unless body.show-topology)
const topo = document.getElementById('topology');

const topoCtx = topo.getContext('2d');

const topoArrows = []; // { from, to, color, ttl }

function setText(id, value) {
  const el = document.getElementById(id);

  if (el) el.textContent = value;
}

// ── Connection ──────────────────────────────────────────────────────
// The socket reconnects on its own with a capped backoff. The page is never
// reloaded, so scroll position, theme and the open view survive a restart of
// the orchestrator. While either the socket or the HTTP polls are failing a
// banner says so.

const connBanner = document.getElementById('conn-banner');

let ws = null;

let wsOpen = true;

let httpOk = true;

let reconnectDelayMs = 1000;

let reconnectTimer = null;

function refreshConnBanner() {
  connBanner.hidden = wsOpen && httpOk;
}

function setHttpOk(ok) {
  if (httpOk === ok) return;

  httpOk = ok;
  refreshConnBanner();
}

function scheduleReconnect() {
  if (reconnectTimer) return;

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, reconnectDelayMs);
  reconnectDelayMs = Math.min(reconnectDelayMs * 2, 8000);
}

function connect() {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';

  ws = new WebSocket(`${scheme}://${location.host}/ws`);

  ws.onopen = () => {
    const wasDown = !wsOpen;

    wsOpen = true;
    reconnectDelayMs = 1000;
    refreshConnBanner();

    if (wasDown) reconnectFeeds();
  };

  ws.onclose = () => {
    wsOpen = false;
    refreshConnBanner();
    scheduleReconnect();
  };

  ws.onerror = () => {
    try {
      ws.close();
    } catch (_e) {
      // Already closing.
    }
  };

  ws.onmessage = onSocketMessage;
}

function onSocketMessage(msg) {
  let m = null;

  try {
    m = JSON.parse(msg.data);
  } catch (_e) {
    return;
  }

  switch (m.type) {
    case 'backfill':
      m.data.slice().reverse().forEach((e) => {
        if (e.type === 'multi_sector_correlation') return; // a replayed fused alert would be jarring
        handleEvent(e);
      });
      pushLatestBbox(m.data);
      break;
    case 'event':
      handleEvent(m.data);
      pushBboxOverlay(m.data);
      break;
    case 'alert': enqueueAlert(m.data); break;
    // Back-compat: legacy WS event name from before the rename. Keep
    // listening so a stale orchestrator and a new dashboard still surface
    // alerts during a partial roll-out.
    case 'fused': enqueueAlert(m.data); break;
    case 'triggers': renderTriggers(m.data); break;
    case 'zones': renderZones(m.data); break;
    case 'cloud': setCloudState(m.data.up); break;
    case 'jobs': renderJobs(m.data); break;
    case 'metrics': renderMetrics(m.data); break;
    case 's3': renderS3(m.data); break;
    default: break;
  }
}

function pushLatestBbox(events) {
  if (!Array.isArray(events) || !events.length) return;

  const recent = events[events.length - 1];

  if (recent && recent.node && recent.yolo_hits) pushBboxOverlay(recent);
}

connect();

// ── Events ──────────────────────────────────────────────────────────

function handleEvent(e) {
  if (!e || !e.node || e.node === 'fusion-node') return;

  // renderEvent de-duplicates, so a backfill after a reconnect does not
  // double count the same event.
  if (!renderEvent(e)) return;

  const sector = e.node;

  lastSeen[sector] = Date.now();
  lastEventTs[sector] = e.ts;

  if (e.queued_offline) queuedOfflineCount += 1;

  if (e.gemini_description) bumpAnalystReachback();

  updateSectorStatus(sector, 'event');
  updateOverlay(sector, e);
  pingTopology(sector);
  reflectQueueCount();
}

// Analyst reachback counter, rolling 60s. The header pill pulses when a new
// call lands so the cloud's per-event participation is visible, not static.
function bumpAnalystReachback() {
  analystCallTimestamps.push(Date.now());
  refreshAnalystPill(true);
}

function refreshAnalystPill(flash = false) {
  const cutoff = Date.now() - 60000;

  while (analystCallTimestamps.length && analystCallTimestamps[0] < cutoff) {
    analystCallTimestamps.shift();
  }

  const valueEl = document.getElementById('metric-gemini');

  if (valueEl) valueEl.textContent = String(analystCallTimestamps.length);

  setText('arch-counter-gemini-flow', `${analystCallTimestamps.length} /min`);
  setFlowRate('arch-flow-gemini', analystCallTimestamps.length);

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

// Let an old call roll off after 60s even when no new events arrive.
setInterval(() => refreshAnalystPill(false), 250);

// De-dup events: same sector + same ts + same detections render once.
// (Backfill on a socket reconnect can otherwise re-emit events the live
// stream already delivered.)
const _seenEvents = new Map(); // node -> Set of dedup keys

function _dedupKey(e) {
  const labels = (e.yolo_hits || []).map((h) => `${h.label}:${(h.confidence || 0).toFixed(2)}`).join('|');

  return `${e.ts || 0}:${labels}`;
}

// Group labels into counts: ['person', 'person', 'backpack'] -> "person ×2, backpack".
function summarizeLabels(labels) {
  const counts = new Map();

  for (const label of labels) {
    const key = String(displayLabel(label));

    counts.set(key, (counts.get(key) || 0) + 1);
  }

  return Array.from(counts, ([key, n]) => (n > 1 ? `${key} ×${n}` : key)).join(', ');
}

// Trim a ticker so only whole rows show. In the stage layout the ticker has a
// fixed height; in the stacked layout it is auto height and nothing is cut.
function trimTicker(container) {
  while (container.children.length > 0 && container.scrollHeight > container.clientHeight + 1) {
    container.lastChild.remove();
  }
}

// Returns true when the event was new and rendered.
function renderEvent(e) {
  const container = document.getElementById(`events-${e.node}`);

  if (!container) return false;

  let seen = _seenEvents.get(e.node);

  if (!seen) {
    seen = new Set();
    _seenEvents.set(e.node, seen);
  }

  const key = _dedupKey(e);

  if (seen.has(key)) return false;

  seen.add(key);

  // Cap memory: keep only the last 50 keys per node.
  if (seen.size > 50) {
    const first = seen.values().next().value;

    seen.delete(first);
  }

  const hits = e.yolo_hits || [];
  const isEmpty = hits.length === 0;
  // The sensor sets model_versions.gemini when the cloud analyst weighed in
  // on this detection. The row carries a pill so the audience can see which
  // detections were replayed through it.
  const usedAnalyst = !!(e.model_versions && e.model_versions.gemini);

  const root = document.createElement('div');

  root.className =
    'event' +
    (e.queued_offline ? ' queued' : '') +
    (isEmpty ? ' empty' : '') +
    (usedAnalyst ? ' analyst-replay' : '');

  const row1 = document.createElement('div');

  row1.className = 'event-row1';

  const yolo = document.createElement('span');

  yolo.className = 'yolo' + (isEmpty ? ' empty' : '');

  if (isEmpty) {
    yolo.textContent = 'empty';
  } else {
    // Each label gets its own span so it can take its class color
    // (person green, backpack amber, drone red).
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

  if (usedAnalyst) {
    const pill = document.createElement('span');

    pill.className = 'analyst-pill';
    pill.textContent = 'Analyst replay';
    row1.appendChild(pill);
  }

  const ts = document.createElement('span');

  ts.className = 'ts';
  ts.textContent = new Date(e.ts * 1000).toLocaleTimeString() + (e.queued_offline ? ' · replayed' : '');
  row1.appendChild(ts);
  root.appendChild(row1);

  // Empty events skip the description row: nothing to describe.
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

  // At most 8 rows per panel on the stage (5 in the scrolling layout), then
  // trim to what fits.
  const maxRows = window.matchMedia('(min-width: 1100px) and (min-height: 720px)').matches ? 8 : 5;

  while (container.children.length > maxRows) container.lastChild.remove();

  trimTicker(container);

  return true;
}

function updateOverlay(sector, e) {
  const side = sector === 'sensor-north' ? 'north' : 'south';
  const labelEl = document.getElementById(`overlay-${side}-label`);
  const tsEl = document.getElementById(`overlay-${side}-ts`);

  if (!labelEl || !tsEl) return;

  const labels = summarizeLabels((e.yolo_hits || []).map((h) => h.label));

  labelEl.textContent = labels || 'awaiting motion';
  tsEl.textContent = new Date(e.ts * 1000).toLocaleTimeString();
  lastEventLabel[sector] = labels;
}

// Per-sector tracking so updateSectorStatus can decide the final state from
// several inputs (job state from /jobs, event freshness from the socket).
const sensorJobRunning = { 'sensor-north': null, 'sensor-south': null };

function updateSectorStatus(node, signal) {
  // Priority order, because the signals arrive on independent timers:
  //   1. Job not running (from /jobs poll)  -> "stopped"
  //   2. Recent event arrived (socket)      -> "live"
  //   3. No event in the last 8s (watchdog) -> "offline"
  //   4. Boot, nothing observed yet         -> "connecting"
  const el = document.getElementById(`status-${node}`);

  if (!el) return;

  const jobRunning = sensorJobRunning[node];

  let status;

  if (jobRunning === false) {
    // The cluster says the sensor's job is stopped; nothing else matters.
    status = 'stopped';
  } else if (signal === 'live' || (signal === 'event' && jobRunning !== false)) {
    status = 'live';
  } else if (signal === 'offline' && jobRunning !== false) {
    status = 'offline';
  } else if (jobRunning === true) {
    // Job running but no event signal yet: keep the existing state unless
    // we are at boot.
    status = el.textContent === '' ? 'connecting' : el.textContent;
  } else {
    status = 'connecting';
  }

  el.textContent = status;
  el.className = 'sector-status ' + status;

  // Mirror onto the zone card's status so the right column agrees with the
  // camera tile.
  const zoneEl = document.getElementById(`zone-status-${node}`);

  if (zoneEl) {
    zoneEl.textContent = status;
    zoneEl.className = 'zone-card-status ' + status;
  }
}

// ── Zone tally (the merge) and the crowd flag ───────────────────────
// renderZones drives the always-visible per-zone counts and combined total.
// The crowd alert renders inside the combined card (its .over state plus an
// in-card alert line), never as a floating interstitial.
//
// The server decides `over` from live counts, and the /zones poll rewrites it
// twice a second. A crowd alert (a real crossing, or F3's rehearsal) must
// still hold on screen, so it latches the flag for ALERT_HOLD_MS.

const ZONE_ORDER = ['sensor-north', 'sensor-south'];

const ALERT_HOLD_MS = 6000;

let lastZoneSnapshot = null;

let crowdLatchUntil = 0;

let crowdLatch = null; // { total, north, south } from the latching alert

function crowdText(total, north, south) {
  return `Crowd: ${total} people across both zones (north ${north} + south ${south})`;
}

// Render the live per-zone counts and the combined total. Called from the
// socket 'zones' push and the /zones poll.
function renderZones(z) {
  if (!z) return;

  lastZoneSnapshot = z;

  const counts = z.counts || {};

  for (const zone of ZONE_ORDER) {
    const el = document.getElementById(`zone-count-${zone}`);

    if (el) {
      const n = counts[zone] || 0;

      if (el.textContent !== String(n)) {
        el.textContent = String(n);
        // A small bump so a changing count visibly ticks.
        el.classList.remove('bumped');
        void el.offsetWidth;
        el.classList.add('bumped');
      }

      const unit = document.getElementById(`zone-unit-${zone}`);

      if (unit) unit.textContent = n === 1 ? 'person' : 'people';
    }
  }

  const total = z.total || 0;
  const thresh = z.threshold != null ? z.threshold : 5;

  setText('combined-total', String(total));
  setText('combined-threshold', `/ ${thresh}`);

  // Threshold meter: fill is total/threshold, clamped; it turns red past it.
  const bar = document.getElementById('combined-bar');

  if (bar) {
    const pct = Math.max(0, Math.min(1, total / Math.max(1, thresh)));

    bar.style.width = `${(pct * 100).toFixed(0)}%`;
  }

  const n = counts['sensor-north'] || 0;
  const s = counts['sensor-south'] || 0;

  setText('combined-breakdown', `north ${n} + south ${s} = ${total}`);

  const card = document.getElementById('combined-card');
  const stateEl = document.getElementById('combined-state');
  const latched = Date.now() < crowdLatchUntil;
  const over = !!z.over || latched;

  if (card) card.classList.toggle('over', over);

  if (stateEl) {
    stateEl.textContent = over ? 'FLAG · CROWD' : 'CLEAR';
    stateEl.className = 'combined-state ' + (over ? 'flag' : 'clear');
  }

  // The alert line always occupies its row (min-height in CSS), so the card
  // height and everything below it never shifts; only the text changes. Live
  // counts drive it while they are over; a latched alert supplies its own
  // numbers once they are not.
  const alertEl = document.getElementById('combined-alert');

  if (alertEl) {
    if (z.over) {
      alertEl.textContent = crowdText(total, n, s);
    } else if (latched && crowdLatch) {
      alertEl.textContent = crowdText(crowdLatch.total, crowdLatch.north, crowdLatch.south);
    } else {
      alertEl.textContent = '';

      if (card) card.classList.remove('flash');
    }
  }
}

// Latch the crowd flag and pulse the card once. Only crowd alerts reach here.
function flagCrowd(f) {
  const contacts = f.contacts || [];

  const countFor = (sector) => {
    const c = contacts.find((x) => x.sector === sector);

    return c && c.count != null ? c.count : 0;
  };

  crowdLatch = {
    total: f.total != null ? f.total : countFor('sensor-north') + countFor('sensor-south'),
    north: countFor('sensor-north'),
    south: countFor('sensor-south'),
  };
  crowdLatchUntil = Date.now() + ALERT_HOLD_MS;

  const card = document.getElementById('combined-card');

  if (card) {
    card.classList.add('over');
    card.classList.remove('flash');
    void card.offsetWidth; // restart the one-shot pulse
    card.classList.add('flash');
    clearTimeout(flagCrowd._flash);
    flagCrowd._flash = setTimeout(() => card.classList.remove('flash'), 1500);
  }

  renderZones(lastZoneSnapshot || { counts: {}, total: crowdLatch.total, threshold: f.threshold, over: true });

  // Re-render when the hold ends, without waiting for the next poll.
  clearTimeout(flagCrowd._release);
  flagCrowd._release = setTimeout(() => {
    if (lastZoneSnapshot) renderZones(lastZoneSnapshot);
  }, ALERT_HOLD_MS + 60);
}

// ── Alert tile ──────────────────────────────────────────────────────
// Always visible. STANDBY until a rule fires, then ACTIVE for ALERT_HOLD_MS
// with the rule label, then back to STANDBY with the time of the last alert.

const ALERT_RULE_LABELS = {
  crowd_threshold: 'Crowd, both zones',
  backpack_detected: 'Backpack detected',
  drone_after_update: 'Drone detected',
  synthetic: 'Rehearsal alert',
  // Legacy keys, so a rolling deploy does not show a raw snake_case rule.
  person_with_backpack: 'Backpack detected',
  person_cross_sector: 'Person in both sectors',
};

let alertLastTs = 0;

let alertActiveUntil = 0;

// ISO 8601 UTC, e.g. 2026-05-02T11:48:14Z, for every absolute timestamp.
function fmtIsoUtc(epochSeconds) {
  if (!epochSeconds || !isFinite(epochSeconds)) return 'none';

  const d = new Date(epochSeconds * 1000);

  return d.toISOString().slice(0, 19) + 'Z';
}

function alertDetail(f) {
  const ruleLabel = ALERT_RULE_LABELS[f.rule] || String(f.rule || 'alert').replace(/_/g, ' ');
  const contacts = f.contacts || [];

  if (f.rule === 'crowd_threshold') {
    const part = (sector) => {
      const c = contacts.find((x) => x.sector === sector);

      return c && c.count != null ? c.count : 0;
    };

    return `${ruleLabel}: ${f.total != null ? f.total : '?'} people (north ${part('sensor-north')} + south ${part('sensor-south')})`;
  }

  const sectors = (f.sectors || []).map((s) => s.replace('sensor-', '')).join(' + ');
  const summary = contacts.map((c) => summarizeLabels(c.yolo_hits || []) || 'none').join(' / ');

  return [ruleLabel, sectors, summary].filter((part) => part).join(' · ');
}

function renderAlertTile(f) {
  const tile = document.getElementById('alert-tile');
  const stateEl = document.getElementById('alert-state');
  const detailEl = document.getElementById('alert-detail');
  const metaEl = document.getElementById('alert-meta');

  if (!tile || !stateEl || !detailEl || !metaEl) return;

  stateEl.textContent = 'ACTIVE';
  detailEl.textContent = alertDetail(f);
  alertLastTs = f && f.ts ? f.ts : Date.now() / 1000;
  metaEl.textContent = `last: ${fmtIsoUtc(alertLastTs)}`;

  // Restart the active class so the pulse runs cleanly on each fire.
  tile.classList.remove('active');
  void tile.offsetWidth;
  tile.classList.add('active');

  alertActiveUntil = Date.now() + ALERT_HOLD_MS;
}

// Collapse back to STANDBY once the hold ends. 250ms tick keeps the
// transition within a quarter second.
setInterval(() => {
  const tile = document.getElementById('alert-tile');

  if (!tile || !tile.classList.contains('active') || Date.now() < alertActiveUntil) return;

  tile.classList.remove('active');
  setText('alert-state', 'STANDBY');
  setText('alert-detail', 'no alerts');
}, 250);

// A rule fired. Every alert lights the tile; only the crowd rule flags and
// pulses the combined card, so backpack and drone alerts do not masquerade
// as a crowd.
function enqueueAlert(f) {
  const alert = f || {};

  renderAlertTile(alert);

  if (alert.rule === 'crowd_threshold') {
    flagCrowd(alert);
    pingTopology('sensor-north', '#b3261e');
    pingTopology('sensor-south', '#b3261e');
  } else {
    pingTopology('sensor-north', '#c87a10');
    pingTopology('sensor-south', '#c87a10');
  }
}

// ── Triggers ────────────────────────────────────────────────────────

function renderTriggers(list) {
  const container = document.getElementById('trigger-chips');
  const previous = new Set(knownTriggers);

  knownTriggers.clear();
  (list || []).forEach((t) => knownTriggers.add(t));

  container.replaceChildren();

  for (const t of list || []) {
    const chip = document.createElement('span');
    const display = displayLabel(t);

    chip.className = `chip chip--${String(display).toLowerCase()}`;

    if (!firstTriggerLoad && !previous.has(t)) {
      chip.classList.add('added');
    }

    chip.textContent = display;
    container.appendChild(chip);
  }

  // Ghost drone chip: the same affordance as F4, click to arm. It stays
  // (dashed) whenever drone is not in the active set, so the operator can
  // turn drone detection on mid-demo without the keyboard. It disappears
  // once the server confirms drone is active. Check the display label, not
  // the raw trigger: legacy configs carry "airplane", which renders as
  // drone, and the ghost chip would otherwise sit next to it.
  const displaysDrone = (list || []).some((t) => displayLabel(t).toLowerCase() === 'drone');

  if (!displaysDrone) {
    const ghost = document.createElement('span');

    ghost.className = 'chip chip--drone chip--ghost';
    ghost.textContent = '+ drone';
    ghost.title = 'Click to arm drone detection';
    ghost.setAttribute('role', 'button');
    ghost.setAttribute('tabindex', '0');

    const arm = async () => {
      ghost.classList.add('arming');

      const next = Array.from(new Set([...knownTriggers, 'drone']));

      try {
        await fetch('/triggers', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ triggers: next }),
        });
      } catch (_e) {
        ghost.classList.remove('arming');
      }
    };

    ghost.addEventListener('click', arm);
    ghost.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        arm();
      }
    });
    container.appendChild(ghost);
  }

  // The operator just rolled out drone detection. Skip on first load, or the
  // initial payload would fire the overlay on every refresh.
  if (!firstTriggerLoad && !previous.has('drone') && knownTriggers.has('drone')) {
    showPipelineUpdateOverlay('drone');
  }

  firstTriggerLoad = false;
}

// Full-viewport "pipeline updated" overlay for ~6s when the active trigger
// set gains a class: the operator rolled out the new class, the cluster
// picked it up, sensors will now flag it.
function showPipelineUpdateOverlay(newClass) {
  const overlay = document.getElementById('pipeline-update-overlay');

  if (!overlay) return;

  const subjectEl = overlay.querySelector('.pipeline-update-class');

  if (subjectEl) subjectEl.textContent = displayLabel(newClass).toUpperCase();

  overlay.classList.remove('show');
  void overlay.offsetWidth; // restart the entry transition
  overlay.classList.add('show');
  overlay.setAttribute('aria-hidden', 'false');
  clearTimeout(showPipelineUpdateOverlay._timer);
  showPipelineUpdateOverlay._timer = setTimeout(() => {
    overlay.classList.remove('show');
    overlay.setAttribute('aria-hidden', 'true');
  }, 6000);
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

  let running = 0;
  let total = 0;
  let failed = 0;
  let anySensorRunning = false;

  for (const j of list || []) {
    total += 1;

    // Case-insensitive: the WS payload sometimes sends "Running" and
    // sometimes "running", depending on the synthetic fallback or
    // `expanso-cli job list`.
    const status = String(j.status || '').toLowerCase();

    if (status === 'running') running += 1;

    if (status === 'failed') failed += 1;

    if (status === 'running' && /^sensor-/.test(j.name)) anySensorRunning = true;

    const item = document.createElement('span');

    item.className = 'job-inline ' + (status || 'pending');
    item.title = `${j.name} · ${status || 'pending'}` + (j.role ? ` · ${j.role}` : '');

    const led = document.createElement('span');

    led.className = 'job-inline-led';
    item.appendChild(led);

    const name = document.createElement('span');

    name.className = 'job-inline-name';
    name.textContent = j.name.replace(/^edge-/, '');
    item.appendChild(name);

    const statusEl = document.createElement('span');

    statusEl.className = 'job-inline-status';
    statusEl.textContent = (status || 'pending').toUpperCase();
    item.appendChild(statusEl);
    container.appendChild(item);
  }

  setText('footer-jobs', `${running}/${total}` + (failed ? ` (${failed} failed)` : ''));

  // Mirror to the ARCH control-plane counter and particle rate. Job state is
  // usually steady (4/4), so the rate is derived from the running count.
  setText('arch-counter-jobs', `${running}/${total} jobs`);
  jobsRollingPush(running);
  setFlowRate('arch-flow-jobs', jobsChangePerMinute());

  // Dim EDGE in the tier strip when no sensor-* job is running. (ALERTS
  // dimming is implicit: if the fusion node is down this page is not
  // rendering. CLOUD dimming follows the cloud-down state.)
  document.body.classList.toggle('tier-edge-down', !anySensorRunning);

  // Per-sector job state for the sector badges ("stopped" vs "live" vs
  // "connecting").
  for (const sectorName of Object.keys(sensorJobRunning)) {
    const job = (list || []).find((j) => j.name === sectorName);

    sensorJobRunning[sectorName] = job ? String(job.status || '').toLowerCase() === 'running' : null;
    updateSectorStatus(sectorName, 'jobs');
  }
}

// ── Metrics ─────────────────────────────────────────────────────────

function renderMetrics(m) {
  if (!m) return;

  const epm = Math.round(m.events_per_minute || 0);

  setText('metric-epm', String(epm));
  setText('metric-fused', String(m.fused_alerts || 0));
  setText('footer-events', String(m.total_events || 0));
  setText('footer-signed', String(m.signed_events || 0));
  setText('footer-fused', String(m.fused_alerts || 0));

  // Mirror to the ARCH counters: the camera stem shows the input rate.
  setText('arch-counter-rtsp', `${epm} ev/min`);
  setFlowRate('arch-flow-rtsp', epm);
  // There is no fused/min metric, so derive a 60s rolling rate client side.
  fusedRollingPush(m.fused_alerts || 0);

  const fpm = fusedPerMinute();

  setText('arch-counter-local', `${fpm} fused/min`);
  setFlowRate('arch-flow-local', fpm);
  setText('arch-jet-epm', String(epm));
  setText('arch-jet-fps', archFpsString(epm));
  setText('arch-jet-age', archLastFrameAgeString(epm));
  setText('arch-jet-queued', String(m.queued_offline || 0));
}

const fusedHistory = []; // { t: ms, total: number }

function fusedRollingPush(totalFused) {
  const now = Date.now();

  fusedHistory.push({ t: now, total: totalFused });

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

// ── ARCH particle-flow speed ────────────────────────────────────────
// Map a per-minute rate to an animation duration: faster rates, faster dots.
// 0 -> 6s (standby), >=120/min -> 0.4s.
function rateToDurationSec(perMin) {
  const r = Math.max(0, Number(perMin) || 0);

  if (r <= 0) return 6.0;

  const minDur = 0.4;
  const maxDur = 3.0;
  const peak = 120;
  const t = Math.min(1, r / peak);

  return Math.max(minDur, maxDur - (maxDur - minDur) * t);
}

function setFlowRate(elementId, perMin) {
  const el = document.getElementById(elementId);

  if (!el) return;

  el.style.animationDuration = `${rateToDurationSec(perMin).toFixed(2)}s`;
}

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
  // Floor at 6/min so the cloud-to-edge dot keeps a visible heartbeat.
  return Math.max(6, jobsChangeStamps.length);
}

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

// Frames per second: count MJPEG frame loads on the two camera feeds in the
// last 5s. The <img src="/stream/..."> element fires `load` for each frame.
const _archFrameStamps = []; // ms timestamps

function _onMjpegFrame() {
  _archFrameStamps.push(Date.now());
}

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

  // With the cameras off screen MJPEG is not loading: derive a coarse rate
  // from events/min (each event is about one detection frame).
  const proxy = (Number(eventsPerMin) || 0) / 30;

  if (proxy > 0) return `~${proxy.toFixed(1)}`;

  return 'none';
}

function archLastFrameAgeString(eventsPerMin) {
  if (_archFrameStamps.length) {
    const last = _archFrameStamps[_archFrameStamps.length - 1];
    const ageS = (Date.now() - last) / 1000;

    if (ageS < 1) return '<1s';

    if (ageS < 60) return `${Math.round(ageS)}s`;

    return `${Math.round(ageS / 60)}m`;
  }

  if ((Number(eventsPerMin) || 0) > 0) return 'live';

  return 'none';
}

// ── Polls ───────────────────────────────────────────────────────────
// Fast poll for the event-driven counters (/metrics), S3 freshness and the
// zone tally: pure in-memory reads, so 500ms is cheap. A failure raises the
// connection banner.
setInterval(async () => {
  try {
    const [m, s, z] = await Promise.all([
      fetch('/metrics').then((r) => r.json()),
      fetch('/s3').then((r) => r.json()),
      fetch('/zones').then((r) => r.json()),
    ]);

    setHttpOk(true);
    renderMetrics(m);
    renderS3(s);
    renderZones(z);
  } catch (_e) {
    setHttpOk(false);
  }
}, 500);

// Slow poll for /jobs: it shells out to `expanso-cli job list` with a 1s
// subprocess timeout, so keep it at 2s to avoid overlapping spawns.
setInterval(async () => {
  try {
    const j = await fetch('/jobs').then((r) => r.json());

    renderJobs(j.jobs || []);
  } catch (_e) {
    // Offline; the fast poll reports it.
  }
}, 2000);

// A sector is offline if no event arrived in 8 seconds.
setInterval(() => {
  const now = Date.now();

  for (const node of Object.keys(lastSeen)) {
    if (lastSeen[node] && now - lastSeen[node] > 8000) updateSectorStatus(node, 'offline');
  }
}, 250);

// ── Cloud egress / S3 archive tile ──────────────────────────────────

let s3LastCount = 0;

function fmtAge(seconds) {
  if (seconds == null || !isFinite(seconds)) return 'none';

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
  // Derive object-delta/min from a rolling window of /s3 polls so the dot
  // speeds up as archive throughput climbs.
  s3RollingPush(state.object_count);
  setFlowRate('arch-flow-s3', s3DeltaPerMinute());

  // Pulse the count when new objects arrive: the literal "data is moving".
  if (state.object_count > s3LastCount && countWrap) {
    countWrap.classList.remove('bumped');
    void countWrap.offsetWidth;
    countWrap.classList.add('bumped');
  }

  s3LastCount = state.object_count;

  if (state.last_poll_ok === false) {
    stateEl.textContent = 'poll error';
    stateEl.className = 'egress-state error';
    lastEl.textContent = state.last_error ? `err: ${state.last_error.slice(0, 60)}` : 'last upload: none';
  } else if (state.last_upload_ts == null) {
    stateEl.textContent = 'awaiting first upload';
    stateEl.className = 'egress-state offline';
    lastEl.textContent = 'last upload: none';
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

  for (const obj of state.recent_keys || []) {
    const li = document.createElement('li');

    li.dataset.key = obj.key;

    const k = document.createElement('button');

    k.type = 'button';
    k.className = 'key key-btn';
    k.textContent = obj.key;
    k.addEventListener('click', () => openS3Modal(obj.key, k));
    li.appendChild(k);

    const t = document.createElement('span');

    t.className = 'ts';
    t.textContent = fmtAge(Date.now() / 1000 - obj.last_modified);
    li.appendChild(t);
    recentEl.appendChild(li);
  }
}

// Pretty-print archive objects: one JSON document, or JSON Lines (one
// document per line, as the archive pipeline writes them). Lines that do not
// parse, such as a truncated tail, are kept as they are.
function prettyJson(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch (_e) {
    // Not a single document; try JSON Lines.
  }

  let parsedAny = false;

  const out = [];

  for (const line of String(text).split('\n')) {
    if (!line.trim()) continue;

    try {
      out.push(JSON.stringify(JSON.parse(line), null, 2));
      parsedAny = true;
    } catch (_e) {
      out.push(line);
    }
  }

  return parsedAny ? out.join('\n\n') : String(text);
}

const s3Modal = document.getElementById('s3-modal');

let s3Opener = null;

async function openS3Modal(key, opener) {
  const title = document.getElementById('s3-modal-title');
  const body = document.getElementById('s3-modal-body');

  s3Opener = opener || document.activeElement;
  title.textContent = key;
  body.textContent = 'loading…';
  s3Modal.classList.add('show');
  document.getElementById('s3-modal-close').focus();

  try {
    const r = await fetch(`/s3/object?key=${encodeURIComponent(key)}`);
    const data = await r.json();

    if (data.error) {
      body.textContent = `error: ${data.error}`;

      return;
    }

    body.textContent = prettyJson(data.body);

    if (data.truncated) body.textContent += '\n\n… (truncated)';
  } catch (e) {
    body.textContent = `fetch failed: ${e}`;
  }
}

function closeS3Modal() {
  if (!s3Modal.classList.contains('show')) return;

  s3Modal.classList.remove('show');

  if (s3Opener && s3Opener.isConnected) s3Opener.focus();

  s3Opener = null;
}

document.getElementById('s3-modal-close').addEventListener('click', closeS3Modal);

// A click on the backdrop (not the card) closes the modal.
s3Modal.addEventListener('click', (ev) => {
  if (ev.target === s3Modal) closeS3Modal();
});

// ── Topology mini canvas ────────────────────────────────────────────

const NODES = {
  'sensor-north': { x: 50, y: 40, label: 'N' },
  'sensor-south': { x: 50, y: 120, label: 'S' },
  'fusion-node': { x: 230, y: 80, label: 'F' },
};

function pingTopology(sensor, color = '#2f9e5a') {
  topoArrows.push({ from: sensor, to: 'fusion-node', color, ttl: 1.0 });
}

function drawTopology() {
  // Hidden by default; skip the work unless body.show-topology is set.
  if (!document.body.classList.contains('show-topology')) {
    topoArrows.length = 0;

    return;
  }

  topoCtx.clearRect(0, 0, topo.width, topo.height);

  topoCtx.strokeStyle = '#7d8b83';
  topoCtx.lineWidth = 2;

  for (const sensor of ['sensor-north', 'sensor-south']) {
    const a = NODES[sensor];
    const b = NODES['fusion-node'];

    topoCtx.beginPath();
    topoCtx.moveTo(a.x, a.y);
    topoCtx.lineTo(b.x, b.y);
    topoCtx.stroke();
  }

  for (const arr of topoArrows) {
    const a = NODES[arr.from];
    const b = NODES[arr.to];

    if (!a || !b) continue;

    const t = 1 - arr.ttl;

    topoCtx.fillStyle = arr.color;
    topoCtx.globalAlpha = arr.ttl;
    topoCtx.beginPath();
    topoCtx.arc(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, 6, 0, Math.PI * 2);
    topoCtx.fill();
    topoCtx.globalAlpha = 1;
    arr.ttl -= 0.04;
  }

  for (let i = topoArrows.length - 1; i >= 0; i--) if (topoArrows[i].ttl <= 0) topoArrows.splice(i, 1);

  for (const [name, n] of Object.entries(NODES)) {
    topoCtx.fillStyle = name === 'fusion-node' ? '#2c4f80' : '#17663a';
    topoCtx.beginPath();
    topoCtx.arc(n.x, n.y, 14, 0, Math.PI * 2);
    topoCtx.fill();
    topoCtx.fillStyle = '#f7f9f7';
    topoCtx.font = 'bold 14px "IBM Plex Mono", monospace';
    topoCtx.textAlign = 'center';
    topoCtx.textBaseline = 'middle';
    topoCtx.fillText(n.label, n.x, n.y);
  }
}

setInterval(drawTopology, 100);

// ── Operator keys ───────────────────────────────────────────────────
// F1 WAN down, F2 WAN up, F3 crowd rehearsal, F4 add drone. Ignored while
// typing in a field. Escape closes the archive object (the keys popover has
// its own handler in ui.js).

async function sendOperator(path, body) {
  try {
    await fetch(path, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (_e) {
    // Backend unreachable; the connection banner already says so.
  }
}

document.addEventListener('keydown', (ev) => {
  if (ev.target && (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA')) return;

  switch (ev.key) {
    case 'Escape': closeS3Modal(); break;
    case 'F1': ev.preventDefault(); sendOperator('/demo/wan-down'); break;
    case 'F2': ev.preventDefault(); sendOperator('/demo/wan-up'); break;
    case 'F3': ev.preventDefault(); sendOperator('/demo/fused-test'); break;
    case 'F4':
      ev.preventDefault();
      // The end state is exactly person + backpack + drone: one new chip
      // pulses in. That is the whole trigger-bar story.
      sendOperator('/triggers', { triggers: ['person', 'backpack', 'drone'] });
      break;
    default: break;
  }
});
