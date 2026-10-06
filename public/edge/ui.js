// Dashboard chrome: theme toggle, tabs, auto-rotate, keyboard help, and the
// ARCH diagram curves. No data handling lives here; see ws_client.js.

const TABS = ['ops', 'arch', 'archive'];

const THEME_KEY = 'edge-isr-theme';

// ── Theme ───────────────────────────────────────────────────────────
// Light is the default (theme.js sets it before first paint). The toggle is
// the only way to reach dark; the choice is remembered when storage works.

const themeButton = document.getElementById('theme-toggle');

function applyTheme(dark) {
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  themeButton.setAttribute('aria-pressed', dark ? 'true' : 'false');
}

function saveTheme(dark) {
  try {
    localStorage.setItem(THEME_KEY, dark ? 'dark' : 'light');
  } catch (_e) {
    // Storage can be blocked (private window); the theme still applies.
  }
}

themeButton.addEventListener('click', () => {
  const dark = document.documentElement.getAttribute('data-theme') !== 'dark';

  applyTheme(dark);
  saveTheme(dark);
});

applyTheme(document.documentElement.getAttribute('data-theme') === 'dark');

// ── Tabs (URL-hash routed, bookmarkable) ────────────────────────────
// #ops, #arch and #archive. Tabs are real anchors, so modified clicks still
// open a new browser tab. Plain clicks and arrow keys switch in place without
// scrolling the page.

const tabList = document.querySelector('.tab-switcher');

const tabEls = Array.from(document.querySelectorAll('.tab-switcher .tab'));

let currentTab = 'ops';

function tabFromHash() {
  const raw = (window.location.hash || '').slice(1).toLowerCase();

  return TABS.includes(raw) ? raw : null;
}

function showTab(target) {
  currentTab = target;
  document.body.classList.remove('tab-ops', 'tab-arch', 'tab-archive');
  document.body.classList.add(`tab-${target}`);

  for (const a of tabEls) {
    const active = a.dataset.tab === target;

    a.classList.toggle('is-active', active);
    a.setAttribute('aria-selected', active ? 'true' : 'false');
    a.tabIndex = active ? 0 : -1;
  }

  for (const name of TABS) {
    const panel = document.getElementById(`panel-${name}`);

    if (panel) panel.hidden = name !== target;
  }

  // The ARCH curves need live geometry, which only exists once the panel
  // is displayed.
  if (target === 'arch') requestAnimationFrame(updateAllArchCurves);
}

function goToTab(name, replace) {
  const url = `#${name}`;

  if (replace) history.replaceState(null, '', url);
  else history.pushState(null, '', url);

  showTab(name);
  holdRotation();
}

for (const a of tabEls) {
  a.addEventListener('click', (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button !== 0) return;

    ev.preventDefault();
    goToTab(a.dataset.tab, false);
  });
}

// WAI-ARIA tabs: arrows move and activate, Home and End jump.
tabList.addEventListener('keydown', (ev) => {
  const i = tabEls.indexOf(document.activeElement);

  if (i < 0) return;

  let next = -1;

  switch (ev.key) {
    case 'ArrowRight': next = (i + 1) % tabEls.length; break;
    case 'ArrowLeft': next = (i - 1 + tabEls.length) % tabEls.length; break;
    case 'Home': next = 0; break;
    case 'End': next = tabEls.length - 1; break;
    default: return;
  }

  ev.preventDefault();
  goToTab(tabEls[next].dataset.tab, true);
  tabEls[next].focus();
});

function applyTabFromHash() {
  const target = tabFromHash();

  if (target && target !== currentTab) showTab(target);
}

window.addEventListener('hashchange', () => {
  applyTabFromHash();
  holdRotation();
});

window.addEventListener('popstate', applyTabFromHash);

showTab(tabFromHash() || 'ops');

// ── Auto-rotate: OPS <-> ARCH for an unattended booth display ───────
// Every ROTATE_MS. A manual tab change holds the carousel for
// ROTATE_IDLE_MS. The pause/resume button is the WCAG pause control. Rotation
// is off by default under prefers-reduced-motion and on phone or tablet
// layouts, where a view flipping under a scrolling reader is hostile.
// replaceState keeps the cycling out of the back-history.

const ROTATE_VIEWS = ['ops', 'arch'];

const ROTATE_MS = 5000;

const ROTATE_IDLE_MS = 45000;

const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const stageLayout = window.matchMedia('(min-width: 1100px) and (min-height: 720px)');

const rotateButton = document.getElementById('rotate-toggle');

const rotateLabel = document.getElementById('rotate-label');

const rotateIcon = document.getElementById('rotate-icon');

const ICON_PAUSE = 'M7 5h4v14H7zM13 5h4v14h-4z';

const ICON_PLAY = 'M8 5v14l11-7z';

let rotateWanted = !reduceMotion.matches && stageLayout.matches;

let rotateTimer = null;

let rotateHold = null;

function rotateTick() {
  const i = ROTATE_VIEWS.indexOf(currentTab);

  if (i < 0) return;

  const next = ROTATE_VIEWS[(i + 1) % ROTATE_VIEWS.length];

  history.replaceState(null, '', `#${next}`);
  showTab(next);
}

function syncRotation() {
  clearInterval(rotateTimer);
  rotateTimer = null;

  if (rotateWanted && !rotateHold) rotateTimer = setInterval(rotateTick, ROTATE_MS);

  rotateLabel.textContent = rotateWanted ? 'Pause rotation' : 'Resume rotation';
  rotateIcon.setAttribute('d', rotateWanted ? ICON_PAUSE : ICON_PLAY);
}

function holdRotation() {
  if (!rotateWanted) return;

  clearTimeout(rotateHold);
  rotateHold = setTimeout(() => {
    rotateHold = null;
    syncRotation();
  }, ROTATE_IDLE_MS);
  syncRotation();
}

rotateButton.addEventListener('click', () => {
  rotateWanted = !rotateWanted;
  clearTimeout(rotateHold);
  rotateHold = null;
  syncRotation();
});

syncRotation();

// A page opened on an explicit view (a bookmark, a shared link) is a manual
// choice, so the carousel waits before moving it.
if (tabFromHash()) holdRotation();

// ── Keyboard help popover ───────────────────────────────────────────

const keysButton = document.getElementById('keys-toggle');

const keysPop = document.getElementById('keys-pop');

function setKeysOpen(open) {
  keysPop.hidden = !open;
  keysButton.setAttribute('aria-expanded', open ? 'true' : 'false');
}

keysButton.addEventListener('click', () => setKeysOpen(keysPop.hidden));

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && !keysPop.hidden) {
    setKeysOpen(false);
    keysButton.focus();
  }
});

document.addEventListener('click', (ev) => {
  if (!keysPop.hidden && !ev.target.closest('.keys')) setKeysOpen(false);
});

// ── ARCH view: anchor SVG curves to live element geometry ───────────
// SVG paths cannot follow elements declaratively. Read the live geometry of
// the source and destination boxes with getBoundingClientRect() and write
// fresh path data. The SVGs use pixel coordinates (no viewBox), so all the
// math is in SVG-local screen space. Re-runs on load, resize, tab switch and
// any size change of the ARCH panel. Below 56rem the SVG is display: none, its
// size is zero and these functions return early.

// Cubic Bezier with control points biased toward horizontal flow first, then
// bending to the destination: the merging-streams and fan-out look.
function buildCurve(sx, sy, ex, ey) {
  const cpx = sx + (ex - sx) * 0.55;

  return `M ${sx} ${sy} C ${cpx} ${sy}, ${cpx} ${ey}, ${ex} ${ey}`;
}

// Cameras (left) to the Edge box left-middle: two converging curves.
function updateCameraCurves() {
  const svg = document.querySelector('.arch-cam-curves');
  const camN = document.getElementById('arch-tile-cam-north');
  const camS = document.getElementById('arch-tile-cam-south');
  const edge = document.querySelector('.arch-box--jetson');
  const pathN = document.getElementById('cam-curve-north');
  const pathS = document.getElementById('cam-curve-south');

  if (!svg || !camN || !camS || !edge || !pathN || !pathS) return;

  const sR = svg.getBoundingClientRect();

  if (!sR.width || !sR.height) return;

  const nR = camN.getBoundingClientRect();
  const sR2 = camS.getBoundingClientRect();
  const eR = edge.getBoundingClientRect();
  const toX = (x) => x - sR.left;
  const toY = (y) => y - sR.top;
  const ex = toX(eR.left);
  const ey = toY(eR.top + eR.height / 2);

  pathN.setAttribute('d', buildCurve(toX(nR.right), toY(nR.top + nR.height / 2), ex, ey));
  pathS.setAttribute('d', buildCurve(toX(sR2.right), toY(sR2.top + sR2.height / 2), ex, ey));
}

// Edge box right-middle to the three destinations: diverging curves with
// independent particles. Each label chip sits at the midpoint of its curve.
function updateEdgeOutCurves() {
  const svg = document.querySelector('.arch-edge-out-curves');
  const edge = document.querySelector('.arch-box--jetson');
  const dL = document.getElementById('arch-box-dest-local');
  const dG = document.getElementById('arch-box-dest-gemini');
  const dS = document.getElementById('arch-box-dest-s3');
  const pL = document.getElementById('edge-curve-local');
  const pG = document.getElementById('edge-curve-gemini');
  const pS = document.getElementById('edge-curve-s3');
  const mL = document.getElementById('arch-meta-local');
  const mG = document.getElementById('arch-meta-gemini');
  const mS = document.getElementById('arch-meta-s3');

  if (!svg || !edge || !dL || !dG || !dS || !pL || !pG || !pS) return;

  const sR = svg.getBoundingClientRect();

  if (!sR.width || !sR.height) return;

  const eR = edge.getBoundingClientRect();
  const toX = (x) => x - sR.left;
  const toY = (y) => y - sR.top;
  const ox = toX(eR.right);
  const oy = toY(eR.top + eR.height / 2);

  for (const [destEl, pathEl, metaEl] of [[dL, pL, mL], [dG, pG, mG], [dS, pS, mS]]) {
    const dR = destEl.getBoundingClientRect();
    const ex = toX(dR.left);
    const ey = toY(dR.top + dR.height / 2);

    pathEl.setAttribute('d', buildCurve(ox, oy, ex, ey));

    if (metaEl) {
      metaEl.style.left = `${(ox + ex) / 2}px`;
      metaEl.style.top = `${(oy + ey) / 2}px`;
    }
  }
}

function updateAllArchCurves() {
  updateCameraCurves();
  updateEdgeOutCurves();
}

window.addEventListener('resize', () => requestAnimationFrame(updateAllArchCurves));

window.addEventListener('load', () => setTimeout(updateAllArchCurves, 100));

if (window.ResizeObserver) {
  const ro = new ResizeObserver(() => requestAnimationFrame(updateAllArchCurves));
  const av = document.querySelector('.arch-view');

  if (av) ro.observe(av);
}
