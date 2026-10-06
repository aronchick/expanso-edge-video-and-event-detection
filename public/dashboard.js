// Box Transfer Monitor dashboard. Reads /api/state (reconciliation written by
// esc-infer or esc-simulate), /api/detections (per-camera box counts written
// by detect_loop.py) and /api/snapshot/<camera> (annotated frames). Every
// source reports its own state in the status strip so a dead backend is
// visible instead of blank. All text goes in through textContent.
(function () {
  const CAMERAS = [
    { id: 'cam-outside', key: 'outside' },
    { id: 'cam-inside', key: 'inside' },
  ];

  const MAX_EVENT_ROWS = 14;

  const app = {
    baseline: null,
    outside: 0,
    inside: 0,
    apiUp: null,
    state: null,
    detectionsStatus: null,
    detections: null,
    lastDetectionKey: '',
    detectionRows: [],
    snapshotUp: { outside: null, inside: null },
    snapshotBusy: { outside: false, inside: false },
    eventSignature: '',
    rawState: '',
    rawDetections: '',
    footerClicks: 0,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function formatTime(value) {
    if (!value) return 'n/a';

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) return 'n/a';

    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
  }

  function noun(count) {
    const person = app.state && app.state.detect_mode === 'person';

    if (person) return count === 1 ? 'person' : 'people';

    return count === 1 ? 'box' : 'boxes';
  }

  async function getJson(url) {
    try {
      const response = await fetch(url, { cache: 'no-store' });
      let data = null;

      try {
        data = await response.json();
      } catch (error) {
        data = null;
      }

      return { ok: response.ok, status: response.status, data: data };
    } catch (error) {
      return { ok: false, status: 0, data: null };
    }
  }

  function setSource(id, kind, text) {
    const node = $(id);

    node.dataset.kind = kind;
    node.textContent = text;
  }

  function setFeedback(id, kind, text) {
    const node = $(id);

    node.dataset.kind = kind;
    node.textContent = text;
  }

  // ---------- status strip and notices ----------

  function renderSources() {
    const apiNotice = $('api-notice');
    const pipelineNotice = $('pipeline-notice');

    if (app.apiUp === null) return;

    if (!app.apiUp) {
      setSource('src-api', 'err', 'unreachable');
      setSource('src-pipeline', 'err', 'unknown');
    } else {
      setSource('src-api', 'ok', 'reachable');
    }

    apiNotice.hidden = app.apiUp;

    if (app.apiUp && app.state) {
      const status = app.state.pipeline_status;

      if (status === 'running') {
        setSource('src-pipeline', 'ok', 'running');
      } else if (status === 'waiting') {
        setSource('src-pipeline', 'warn', 'waiting for state.json');
      } else {
        setSource('src-pipeline', 'warn', String(status || 'unknown'));
      }

      pipelineNotice.hidden = status !== 'waiting';
    } else {
      pipelineNotice.hidden = true;
    }

    if (!app.apiUp) {
      setSource('src-detector', 'err', 'unknown');
    } else if (app.detectionsStatus === 200) {
      setSource('src-detector', 'ok', 'reporting');
    } else if (app.detectionsStatus === 503) {
      setSource('src-detector', 'warn', 'no detections.json (503)');
    } else if (app.detectionsStatus !== null) {
      setSource('src-detector', 'err', 'error ' + app.detectionsStatus);
    }

    CAMERAS.forEach(function (camera) {
      const id = 'src-cam-' + camera.key;
      const up = app.snapshotUp[camera.key];

      if (up === null) return;

      setSource(id, up ? 'ok' : 'err', up ? 'snapshot ok' : 'no snapshot');
    });
  }

  // ---------- header and session bar ----------

  function renderSession() {
    const state = app.state;

    $('session-id').textContent = state && state.session_id ? state.session_id : 'n/a';
    $('fps').textContent = state ? Number(state.inference_fps || 0).toFixed(1) : 'n/a';
    $('detect-mode').textContent = state && state.detect_mode ? state.detect_mode : 'n/a';
    $('last-update').textContent = state && state.last_event_ts ? formatTime(state.last_event_ts) : 'none yet';
    $('baseline-count').textContent = app.baseline !== null ? String(app.baseline) : 'not set';
  }

  // ---------- reconciliation (departed vs arrived) ----------

  function renderReconciliation() {
    const card = $('recon-card');
    const title = $('recon-status');
    const detail = $('recon-detail');
    const first = $('recon-first');
    const state = app.state;
    const depEl = $('departures');
    const arrEl = $('arrivals');

    first.hidden = true;

    if (!state) {
      depEl.textContent = 'n/a';
      arrEl.textContent = 'n/a';
      card.dataset.state = 'wait';
      title.textContent = 'No data from the API';
      detail.textContent = 'Departures and arrivals come from /api/state.';

      return;
    }

    const dep = Number(state.camera_outside_departures || 0);
    const arr = Number(state.camera_inside_arrivals || 0);
    const disc = Number(state.discrepancy || 0);

    depEl.textContent = String(dep);
    arrEl.textContent = String(arr);

    if (state.pipeline_status === 'waiting') {
      card.dataset.state = 'wait';
      title.textContent = 'Waiting for crossing data';
      detail.textContent = 'No state.json yet. Counts show 0 until a pipeline writes one.';

      return;
    }

    if (dep === 0 && arr === 0 && state.status !== 'DISCREPANCY') {
      card.dataset.state = 'wait';
      title.textContent = 'No crossings yet';
      detail.textContent = '0 departed, 0 arrived';

      return;
    }

    if (state.status === 'MATCH' || disc === 0) {
      card.dataset.state = 'match';
      title.textContent = 'All ' + noun(2) + ' accounted for';
      detail.textContent = dep + ' departed, ' + arr + ' arrived';

      return;
    }

    card.dataset.state = 'short';

    if (disc > 0) {
      title.textContent = disc + ' ' + noun(disc) + ' unaccounted for';
      detail.textContent = dep + ' departed, ' + arr + ' arrived, ' + disc + ' missing';
    } else {
      const extra = Math.abs(disc);

      title.textContent = extra + ' more ' + noun(extra) + ' arrived than departed';
      detail.textContent = dep + ' departed, ' + arr + ' arrived';
    }

    if (state.first_discrepancy_at) {
      first.textContent = 'First detected ' + formatTime(state.first_discrepancy_at);
      first.hidden = false;
    }
  }

  // ---------- inventory against a baseline ----------

  function makeNum(value, side) {
    const span = document.createElement('span');

    span.className = 'num';

    if (side) span.dataset.side = side;

    span.textContent = String(value);

    return span;
  }

  function renderInventory() {
    const banner = $('inventory-banner');
    const title = $('inventory-title');
    const counts = $('inventory-counts');
    const note = $('inventory-baseline');
    const hasDetector = app.detectionsStatus === 200;
    const total = app.outside + app.inside;

    counts.textContent = '';

    if (!hasDetector) {
      $('feed-count-outside').textContent = 'n/a';
      $('feed-count-inside').textContent = 'n/a';
    } else {
      $('feed-count-outside').textContent = String(app.outside);
      $('feed-count-inside').textContent = String(app.inside);
    }

    if (!hasDetector && app.baseline === null) {
      banner.dataset.state = 'wait';
      title.textContent = app.apiUp === false ? 'Detector counts unavailable' : 'No detector data yet';
      note.textContent = app.apiUp === false
        ? 'The dashboard API is not reachable, so per-camera counts cannot load.'
        : 'detect_loop.py has not written detections.json (the API answers 503). Reconciliation above does not need it. A manual baseline still works.';

      return;
    }

    if (!hasDetector) {
      banner.dataset.state = 'wait';
      title.textContent = 'Baseline ' + app.baseline + ' set, detector counts unavailable';
      note.textContent = 'Per-camera counts cannot be compared until detections.json is written.';

      return;
    }

    const labelA = document.createElement('span');
    const labelB = document.createElement('span');
    const labelT = document.createElement('span');

    labelA.append('Location A', makeNum(app.outside, 'out'));
    labelB.append('Location B', makeNum(app.inside, 'in'));
    labelT.append('Total', makeNum(total, ''));
    counts.append(labelA, labelB, labelT);

    if (app.baseline === null) {
      banner.dataset.state = 'wait';
      title.textContent = 'Counting ' + noun(2);
      note.textContent = 'Press Set Baseline to lock the starting count.';
    } else if (total === app.baseline) {
      banner.dataset.state = 'ok';
      title.textContent = 'All ' + app.baseline + ' ' + noun(app.baseline) + ' accounted for';
      note.textContent = 'Baseline: ' + app.baseline + ' ' + noun(app.baseline);
    } else if (total < app.baseline) {
      const missing = app.baseline - total;

      banner.dataset.state = 'short';
      title.textContent = missing + ' ' + noun(missing) + ' missing';
      note.textContent = 'Baseline: ' + app.baseline + '. Current: ' + total + '. Missing: ' + missing + '.';
    } else {
      banner.dataset.state = 'wait';
      title.textContent = 'Total: ' + total + ' ' + noun(total) + ', above the baseline of ' + app.baseline;
      note.textContent = 'Baseline: ' + app.baseline;
    }
  }

  // ---------- event log ----------

  function eventRow(item) {
    const row = document.createElement('div');
    const time = document.createElement('span');
    const src = document.createElement('span');
    const action = document.createElement('span');

    row.className = 'event-row';
    time.className = 'time';
    src.className = 'src';
    time.textContent = formatTime(item.when);
    src.textContent = item.source;
    row.append(time, src);

    if (item.kind === 'detection') {
      action.textContent = 'A: ' + item.outside + '  B: ' + item.inside + '  Total: ' + (item.outside + item.inside);
      row.append(action);

      return row;
    }

    const count = document.createElement('span');

    action.className = item.outsideCamera ? 'depart' : 'arrive';
    action.textContent = item.outsideCamera ? 'Departed' : 'Arrived';
    count.className = 'count';
    count.textContent = 'count ' + item.count;
    row.append(action, count);

    return row;
  }

  function collectEvents() {
    const items = [];
    const events = app.state && Array.isArray(app.state.recent_events) ? app.state.recent_events : [];

    events.slice(-MAX_EVENT_ROWS).forEach(function (evt) {
      const source = evt.camera_id || 'unknown';
      const outsideCamera = source.includes('outside');

      items.push({
        kind: 'crossing',
        when: evt.frame_timestamp,
        sort: Date.parse(evt.frame_timestamp) || 0,
        source: source,
        outsideCamera: outsideCamera,
        count: outsideCamera ? evt.departures || 0 : evt.arrivals || 0,
      });
    });

    app.detectionRows.forEach(function (row) {
      items.push(row);
    });

    items.sort(function (a, b) {
      return b.sort - a.sort;
    });

    return items.slice(0, MAX_EVENT_ROWS);
  }

  function renderEvents() {
    const holder = $('event-scroll');
    const items = collectEvents();
    const signature = JSON.stringify(items);

    if (signature === app.eventSignature) return;

    app.eventSignature = signature;
    holder.textContent = '';

    if (items.length === 0) {
      const empty = document.createElement('p');

      empty.className = 'event-empty';
      empty.textContent = app.apiUp === false
        ? 'No events: the dashboard API is not reachable.'
        : 'Waiting for crossing events';
      holder.append(empty);

      return;
    }

    items.forEach(function (item) {
      holder.append(eventRow(item));
    });
  }

  // ---------- raw state ----------

  function renderRaw() {
    const stateText = app.state ? JSON.stringify(app.state, null, 2) : 'No response from /api/state';

    const detText = app.detections
      ? JSON.stringify(app.detections, null, 2)
      : 'No detector data (status ' + (app.detectionsStatus === null ? 'pending' : app.detectionsStatus) + ')';

    if (stateText !== app.rawState) {
      app.rawState = stateText;
      $('raw-state').textContent = stateText;
    }

    if (detText !== app.rawDetections) {
      app.rawDetections = detText;
      $('raw-detections').textContent = detText;
    }
  }

  function renderAll() {
    renderSources();
    renderSession();
    renderReconciliation();
    renderInventory();
    renderEvents();
    renderRaw();
  }

  // ---------- polling ----------

  async function pollState() {
    const result = await getJson('/api/state');

    app.apiUp = result.status !== 0;
    app.state = result.ok ? result.data : null;
    renderAll();
  }

  async function pollDetections() {
    const result = await getJson('/api/detections');

    app.detectionsStatus = result.status === 0 ? null : result.status;

    if (result.ok && result.data) {
      const outside = result.data['cam-outside'] || {};
      const inside = result.data['cam-inside'] || {};

      app.detections = result.data;
      app.outside = Number(outside.boxes || 0);
      app.inside = Number(inside.boxes || 0);

      const key = app.outside + ':' + app.inside;

      if (key !== app.lastDetectionKey) {
        app.lastDetectionKey = key;
        app.detectionRows.unshift({
          kind: 'detection',
          when: new Date().toISOString(),
          sort: Date.now(),
          source: 'detector',
          outside: app.outside,
          inside: app.inside,
        });
        app.detectionRows = app.detectionRows.slice(0, MAX_EVENT_ROWS);
      }
    } else {
      app.detections = null;
    }

    renderAll();
  }

  function refreshCamera(camera) {
    if (app.snapshotBusy[camera.key]) return;

    app.snapshotBusy[camera.key] = true;

    const img = $('feed-' + camera.key);
    const empty = $('feed-' + camera.key + '-empty');
    const probe = new Image();

    probe.onload = function () {
      app.snapshotBusy[camera.key] = false;
      app.snapshotUp[camera.key] = true;
      img.src = probe.src;
      img.hidden = false;
      empty.hidden = true;
      renderSources();
    };

    probe.onerror = function () {
      app.snapshotBusy[camera.key] = false;
      app.snapshotUp[camera.key] = false;
      img.hidden = true;
      empty.hidden = false;
      empty.textContent = '';

      const head = document.createElement('span');
      const why = document.createElement('span');

      head.textContent = 'No snapshot from ' + camera.id;
      why.className = 'dim';
      why.textContent = app.apiUp === false
        ? 'The dashboard API is not reachable.'
        : 'The API has no snapshots/' + camera.id + '.jpg yet. detect_loop.py writes it.';
      empty.append(head, why);
      renderSources();
    };

    probe.src = '/api/snapshot/' + camera.id + '?t=' + Date.now();
  }

  function refreshCameras() {
    CAMERAS.forEach(refreshCamera);
  }

  // ---------- controls ----------

  async function onSetBaseline() {
    const result = await getJson('/api/detections');

    if (result.ok && result.data) {
      app.outside = Number((result.data['cam-outside'] || {}).boxes || 0);
      app.inside = Number((result.data['cam-inside'] || {}).boxes || 0);
      app.detectionsStatus = 200;
      app.detections = result.data;
    }

    if (app.detectionsStatus !== 200) {
      setFeedback('control-feedback', 'err', 'Baseline not set: no detector counts are available (status ' + (result.status || 'unreachable') + '). Use the manual baseline.');
      $('manual-override').hidden = false;
      renderAll();

      return;
    }

    app.baseline = app.outside + app.inside;
    setFeedback('control-feedback', 'ok', 'Baseline set to ' + app.baseline + ' ' + noun(app.baseline) + '.');
    renderAll();
  }

  async function onReset() {
    app.baseline = null;

    try {
      const response = await fetch('/api/reset', { method: 'POST' });

      if (!response.ok) throw new Error('status ' + response.status);

      setFeedback('control-feedback', 'ok', 'Reset command sent. The baseline is cleared; a new session starts when the pipeline picks it up.');
    } catch (error) {
      setFeedback('control-feedback', 'err', 'Reset failed: the API did not accept the command (' + error.message + '). The baseline is cleared on this page only.');
    }

    renderAll();
  }

  function onManualSet() {
    const value = parseInt($('manual-count').value, 10);

    if (Number.isNaN(value) || value < 1) {
      setFeedback('manual-feedback', 'err', 'Enter a whole number of 1 or more.');

      return;
    }

    app.baseline = value;
    setFeedback('manual-feedback', 'ok', 'Baseline set to ' + value + '.');
    setFeedback('control-feedback', 'ok', 'Baseline set to ' + value + ' by hand.');
    renderAll();
  }

  function revealManual() {
    $('manual-override').hidden = false;
  }

  function onFooterClick() {
    app.footerClicks += 1;

    if (app.footerClicks >= 3) {
      revealManual();
      app.footerClicks = 0;
    }

    window.setTimeout(function () {
      app.footerClicks = 0;
    }, 1000);
  }

  function start() {
    $('baseline-btn').addEventListener('click', onSetBaseline);
    $('reset-btn').addEventListener('click', onReset);
    $('manual-set-btn').addEventListener('click', onManualSet);
    $('footer-area').addEventListener('click', onFooterClick);

    if (window.location.hash === '#manual') revealManual();

    window.setInterval(pollState, 1000);
    window.setInterval(refreshCameras, 1000);
    window.setInterval(pollDetections, 2000);
    pollState();
    pollDetections();
    refreshCameras();
  }

  start();
})();
