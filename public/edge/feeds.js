// Camera tiles: WebRTC (go2rtc) over an MJPEG floor, plus the bbox overlay.
// Loaded before ws_client.js, which calls pushBboxOverlay and displayLabel.

// COCO has no "drone" class: YOLO reports drones as "airplane". Alias the
// display so the narrative reads correctly without custom weights. The
// backend still matches on "airplane".
const _LABEL_ALIASES = { airplane: 'drone' };

function displayLabel(label) {
  return _LABEL_ALIASES[String(label).toLowerCase()] || label;
}

// ── Camera tile feed: MJPEG floor + WebRTC opportunistic ─────────────
// Two layers per tile: the <img class="sector-fallback"> streams MJPEG
// continuously (multipart/x-mixed-replace from /stream/{sector}); the
// <video class="sector-video"> sits on top and is faded in (opacity 1)
// only when WebRTC is proven healthy - negotiation succeeded AND frames
// are actually arriving. Any fault (connection state failed/disconnected,
// no frame for 3s) instantly fades video out, revealing the MJPEG below,
// and schedules a WebRTC retry. The bbox canvas overlay (z-index 2) sits
// above both and is independent of which feed is currently visible.
//
// This model is "always-on bedrock + opportunistic upgrade" - the screen
// is NEVER black. MJPEG runs at ~10fps and gives ~500ms latency; WebRTC
// runs at native ~25fps and ~150ms latency. WebRTC is the nice-to-have.

const GO2RTC_BASE = `${location.protocol}//${location.hostname}:1984`;

// Source dims are detected per-sector from the actual <video>'s videoWidth/Height
// at draw time, NOT hardcoded - sensor's RTSP source can be sub-stream (640×360),
// main (1280×720), or 4K depending on config. Fallback used until video has loaded.
const SECTOR_SOURCE_W_FALLBACK = 640;

const SECTOR_SOURCE_H_FALLBACK = 360;

const _bboxClearTimers = {};    // sector -> setTimeout handle for clear-after-hold

// Which feed layer is live per sector: 'mjpeg' (baked boxes already in the
// image) or 'webrtc' (raw video, needs the canvas overlay). Default mjpeg -
// SectorFeed flips it to webrtc only once frames actually arrive.
const feedMode = { 'sensor-north': 'mjpeg', 'sensor-south': 'mjpeg' };

// Per-class overlay colors - kept in sync with the Python baked-box palette
// (snapshots._CLASS_COLOR_BGR / detector._CLASS_COLOR_BGR).
const _CLASS_COLORS = {
  person: '#3ce65a',    // green
  backpack: '#ffa726',  // amber
  drone: '#f03c3c',     // red
  airplane: '#f03c3c',
};

function classColor(label) {
  return _CLASS_COLORS[String(label).toLowerCase()] || '#ffa726';
}

class SectorFeed {
  constructor(sectorEl) {
    this.stream = sectorEl.dataset.stream;
    this.sector = sectorEl.dataset.sector;
    this.video = document.getElementById(`video-${this.sector}`);
    this.img = document.getElementById(`feed-${this.sector}`);

    if (!this.video || !this.img || !this.stream || !this.sector) return;

    this.pc = null;
    this.lastFrameAt = 0;
    this.healthTimer = null;
    this.retryTimer = null;
    this.mode = 'mjpeg'; // 'mjpeg' | 'webrtc'

    // MJPEG is the floor. The <img> already has src="/stream/{sector}" from
    // index.html; we just guarantee both layers' opacity state up front.
    this.video.style.opacity = '0';
    this.img.style.opacity = '1';

    // Tabs throttled in the background can stall RTCPeerConnection. When the
    // user returns, force a health re-check. If we're already in MJPEG, also
    // try WebRTC again immediately (don't wait for the 15s retry timer).
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && this.mode !== 'webrtc') this.tryWebRTC();
    });

    this.tryWebRTC();
  }

  // Switch to the always-streaming MJPEG layer. Idempotent and safe to call
  // from any state - clears all WebRTC resources and arms a retry.
  toMJPEG(reason) {
    if (this.mode === 'mjpeg' && this.pc === null) return;
    console.warn(`[feed] ${this.sector} → MJPEG (${reason})`);
    this.mode = 'mjpeg';
    feedMode[this.sector] = 'mjpeg';
    this.video.style.opacity = '0';
    this.img.style.opacity = '1';

    if (this.video.srcObject) this.video.srcObject = null;

    if (this.pc) { try { this.pc.close(); } catch (_) {}

 this.pc = null; }

    if (this.healthTimer) { clearInterval(this.healthTimer); this.healthTimer = null; }

    if (this.retryTimer) clearTimeout(this.retryTimer);
    // 15s retry - long enough to not pummel a dead service, short enough to
    // recover quickly when the Jetson sidecar finishes restarting.
    this.retryTimer = setTimeout(() => this.tryWebRTC(), 15_000);
  }

  // Promote to the WebRTC video layer. Only called from the frame watchdog
  // once a frame has actually arrived - we never trust signaling alone.
  toWebRTC() {
    if (this.mode === 'webrtc') return;
    console.log(`[feed] ${this.sector} → WebRTC`);
    this.mode = 'webrtc';
    feedMode[this.sector] = 'webrtc';
    this.video.style.opacity = '1';
    this.img.style.opacity = '0';
  }

  log(...args) {
    console.log(`[feed:${this.sector}]`, ...args);
  }

  async tryWebRTC() {
    if (this.retryTimer) { clearTimeout(this.retryTimer); this.retryTimer = null; }

    if (this.pc) { try { this.pc.close(); } catch (_) {}

 this.pc = null; }

    this.log('tryWebRTC: starting handshake against', `${GO2RTC_BASE}/api/webrtc?src=${this.stream}`);

    try {
      const pc = new RTCPeerConnection({ iceServers: [] });
      this.pc = pc;

      pc.onconnectionstatechange = () => {
        this.log('connectionState =', pc.connectionState);

        if (['failed', 'disconnected', 'closed'].includes(pc.connectionState)) {
          this.toMJPEG(`connectionState=${pc.connectionState}`);
        }
      };

      pc.oniceconnectionstatechange = () => {
        this.log('iceConnectionState =', pc.iceConnectionState);
      };

      pc.onicegatheringstatechange = () => {
        this.log('iceGatheringState =', pc.iceGatheringState);
      };

      pc.onicecandidate = (e) => {
        if (e.candidate) {
          this.log('local ICE candidate:', e.candidate.candidate);
        } else {
          this.log('local ICE candidates: gathering complete (null candidate)');
        }
      };

      pc.ontrack = (e) => {
        this.log('ontrack fired - track kind=', e.track?.kind, 'streams=', e.streams?.length);
        this.video.srcObject = e.streams[0];

        for (const r of pc.getReceivers()) {
          if (r.track && r.track.kind === 'video') r.playoutDelayHint = 0.15;
        }

        this.log('starting frame watchdog');
        this.startFrameWatchdog();
      };

      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });
      this.log('transceivers added (video+audio recvonly)');

      const negotiate = async () => {
        this.log('createOffer…');
        const offer = await pc.createOffer();
        this.log('createOffer done - sdp length=', offer.sdp.length);
        await pc.setLocalDescription(offer);
        this.log('setLocalDescription done; POST to go2rtc…');
        const t0 = performance.now();

        const resp = await fetch(
          `${GO2RTC_BASE}/api/webrtc?src=${encodeURIComponent(this.stream)}`,
          { method: 'POST', body: pc.localDescription.sdp }
        );

        this.log(`fetch returned HTTP ${resp.status} in ${(performance.now() - t0).toFixed(0)}ms`);

        if (!resp.ok) throw new Error(`go2rtc HTTP ${resp.status}`);
        const answer = await resp.text();
        this.log('answer SDP received, length=', answer.length);
        await pc.setRemoteDescription({ type: 'answer', sdp: answer });
        this.log('setRemoteDescription done; awaiting ontrack + first frame');
      };

      await Promise.race([
        negotiate(),
        new Promise((_, rej) =>
          setTimeout(() => rej(new Error('negotiate-timeout')), 12000)),
      ]);
    } catch (err) {
      this.log('ERROR in tryWebRTC:', err.message || err);
      this.toMJPEG(`negotiate: ${err.message || err}`);
    }
  }

  startFrameWatchdog() {
    this.lastFrameAt = performance.now();

    // Frame-accurate detection via requestVideoFrameCallback (Chrome, Edge,
    // Safari 16+). Falls back to a 250ms poll of video.currentTime for
    // older browsers - works but less precise.
    const hasVFC = 'requestVideoFrameCallback' in this.video;
    this.log(`watchdog start: hasVFC=${hasVFC}, video readyState=${this.video.readyState}, videoWidth=${this.video.videoWidth}`);
    let framesSeen = 0;

    if (hasVFC) {
      const onFrame = () => {
        if (!this.pc || this.pc.connectionState === 'closed') return;
        framesSeen++;

        if (framesSeen === 1) this.log('FIRST FRAME via rVFC');

        if (framesSeen % 30 === 0) this.log(`${framesSeen} frames received`);
        this.lastFrameAt = performance.now();

        if (this.mode !== 'webrtc') this.toWebRTC();

        try { this.video.requestVideoFrameCallback(onFrame); } catch (_) {}
      };

      try { this.video.requestVideoFrameCallback(onFrame); } catch (_) {}
    } else {
      let lastTime = -1;

      const poll = setInterval(() => {
        if (!this.pc || this.pc.connectionState === 'closed') {
          clearInterval(poll);

          return;
        }

        if (this.video.readyState >= 2 && this.video.currentTime !== lastTime) {
          lastTime = this.video.currentTime;
          framesSeen++;

          if (framesSeen === 1) this.log('FIRST FRAME via currentTime poll');
          this.lastFrameAt = performance.now();

          if (this.mode !== 'webrtc') this.toWebRTC();
        }
      }, 250);
    }

    // Stall watchdog: if no frame arrives within 3s, fall back. Tolerance
    // is 3s (not 1s) because decoder warm-up and first-frame delivery can
    // legitimately take >1s on slow links even after negotiation succeeds.
    if (this.healthTimer) clearInterval(this.healthTimer);
    this.healthTimer = setInterval(() => {
      if (!this.pc || this.pc.connectionState === 'closed') {
        clearInterval(this.healthTimer);
        this.healthTimer = null;

        return;
      }

      if (performance.now() - this.lastFrameAt > 3000) {
        this.toMJPEG('frame-stall');
      }
    }, 1000);
  }
}

function _drawTrackGate(ctx, x1, y1, x2, y2, color) {
  const leg = Math.max(18, Math.min((x2 - x1) / 4, (y2 - y1) / 4, 44));
  // Thin full rectangle (faint) for the body, bold brackets for the corners -
  // reads as a track gate at booth distance on a 42" monitor.
  ctx.lineJoin = 'miter';
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.45;
  ctx.lineWidth = 2;
  ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
  ctx.globalAlpha = 1;
  ctx.lineWidth = 5;
  ctx.beginPath(); ctx.moveTo(x1, y1 + leg); ctx.lineTo(x1, y1); ctx.lineTo(x1 + leg, y1); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x2 - leg, y1); ctx.lineTo(x2, y1); ctx.lineTo(x2, y1 + leg); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x1, y2 - leg); ctx.lineTo(x1, y2); ctx.lineTo(x1 + leg, y2); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x2 - leg, y2); ctx.lineTo(x2, y2); ctx.lineTo(x2, y2 - leg); ctx.stroke();
}

function _drawLabelChip(ctx, x1, y1, text, color) {
  ctx.font = '700 15px "IBM Plex Mono", ui-monospace, monospace';
  const w = ctx.measureText(text).width;
  const padX = 7, h = 22;
  const top = Math.max(0, y1 - h);
  ctx.fillStyle = color;
  ctx.fillRect(x1, top, w + padX * 2, h);
  ctx.fillStyle = '#0b0d10';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, x1 + padX, top + h / 2 + 1);
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
  // coords (in source frame space - whatever resolution YOLO inferred on)
  // project onto the visible canvas. With `contain`, the video is scaled
  // by min(cw/sw, ch/sh) and centered, leaving letterbox bars on whichever
  // axis has slack. Use Math.min (not max - that would be cover semantics
  // and put boxes outside the video onto the letterbox bars).
  // Source dims: prefer the live WebRTC video; fall back to the MJPEG
  // <img>'s natural size (the snapshot is 1280x720) so overlay coords line
  // up with whichever layer is actually showing; finally the static
  // fallback. Both layers use object-fit: contain, so the projection math
  // (Math.min + center) is identical for either.
  const video = document.getElementById(`video-${sector}`);
  const img = document.getElementById(`feed-${sector}`);
  let sw = (video && video.videoWidth) || 0;
  let sh = (video && video.videoHeight) || 0;

  if (!sw || !sh) { sw = (img && img.naturalWidth) || SECTOR_SOURCE_W_FALLBACK; sh = (img && img.naturalHeight) || SECTOR_SOURCE_H_FALLBACK; }

  const scale = Math.min(cw / sw, ch / sh);
  const renderedW = sw * scale;
  const renderedH = sh * scale;
  const offsetX = (cw - renderedW) / 2;
  const offsetY = (ch - renderedH) / 2;

  for (let i = 0; i < hits.length; i++) {
    const hit = hits[i];

    if (!hit || !hit.bbox) continue;
    const [x1s, y1s, x2s, y2s] = hit.bbox;
    const x1 = x1s * scale + offsetX;
    const y1 = y1s * scale + offsetY;
    const x2 = x2s * scale + offsetX;
    const y2 = y2s * scale + offsetY;
    const color = classColor(hit.label);
    _drawTrackGate(ctx, x1, y1, x2, y2, color);
    const labelText = `${displayLabel(hit.label).toUpperCase()} ${Math.round((hit.confidence || 0) * 100)}%`;
    _drawLabelChip(ctx, x1, y1, labelText, color);
  }
}

// Hook: every WS event with yolo_hits triggers an overlay redraw. Hold ~1s.
// Tradeoff: a longer hold (e.g. 3.5s) bridges gaps between sparse events but
// leaves a stale bracket painted where a fast-moving subject WAS while they
// continue walking - looks like the tracker is drunk. 1s vanishes stale boxes
// before the YOLO/transport lag (~300–500ms) becomes visually distracting,
// while still keeping the bracket up most frames at ~2 events/sec/sector.
function pushBboxOverlay(e) {
  if (!e || !e.node || !e.yolo_hits) return;

  // In MJPEG mode the boxes are already baked into the frame (sensor's
  // detector.annotate, or the synthesized fake feed). Drawing the canvas
  // overlay too would double them up - slightly offset by transport lag -
  // which looks broken. Only overlay on raw WebRTC video.
  if (feedMode[e.node] !== 'webrtc') {
    drawBboxOverlay(e.node, []); // keep the canvas clear

    return;
  }

  drawBboxOverlay(e.node, e.yolo_hits);

  if (_bboxClearTimers[e.node]) clearTimeout(_bboxClearTimers[e.node]);
  _bboxClearTimers[e.node] = setTimeout(() => {
    drawBboxOverlay(e.node, []);
  }, 1000);
}

// Boot one SectorFeed per camera tile (after DOM exists). Each feed owns
// its own MJPEG/WebRTC swap state and health watchdog independently.
window.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.sector-feed[data-stream]').forEach((el) => {
    new SectorFeed(el);
  });
});

// Re-open both MJPEG streams. Called after the dashboard socket reconnects:
// a stream that ended with the orchestrator would otherwise stay frozen on
// its last frame.
function reconnectFeeds() {
  for (const node of ['sensor-north', 'sensor-south']) {
    const img = document.getElementById(`feed-${node}`);

    if (img) img.src = `/stream/${node}?reconnect=${Date.now()}`;
  }
}

// A broken MJPEG connection (sensor restart, orchestrator restart) is not
// retried by the browser. naturalWidth === 0 means the stream never produced
// a frame, so tickle the src.
setInterval(() => {
  for (const node of ['sensor-north', 'sensor-south']) {
    const img = document.getElementById(`feed-${node}`);

    if (img && img.complete && img.naturalWidth === 0) {
      img.src = `/stream/${node}?reconnect=${Date.now()}`;
    }
  }
}, 5000);
