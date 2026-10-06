// Guide page: theme toggle, step explorer paging, copy and download feedback.
// All text from the page is written with textContent, never innerHTML.

const THEME_KEY = 'edge-isr-theme';

const MESSAGE_MS = 9000;

function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch (_error) {
    return null;
  }
}

function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_error) {
    // Private mode or blocked storage: the theme just does not persist.
  }
}

// ── Theme ─────────────────────────────────────────────────────────────

const themeButton = document.getElementById('theme-toggle');

function syncThemeButton() {
  const dark = document.documentElement.getAttribute('data-theme') === 'dark';

  themeButton.setAttribute('aria-pressed', String(dark));
}

themeButton.addEventListener('click', () => {
  const dark = document.documentElement.getAttribute('data-theme') !== 'dark';

  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  storageSet(THEME_KEY, dark ? 'dark' : 'light');
  syncThemeButton();
});

syncThemeButton();

// ── Feedback next to the control that was used ────────────────────────

const timers = new WeakMap();

function say(target, ok, message) {
  window.clearTimeout(timers.get(target));
  target.textContent = message;
  target.setAttribute('data-state', ok ? 'ok' : 'error');
  timers.set(
    target,
    window.setTimeout(() => {
      target.textContent = '';
      target.removeAttribute('data-state');
    }, MESSAGE_MS),
  );
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);

    return true;
  } catch (_error) {
    return false;
  }
}

// Stage outputs are fetched ahead of the click, so Copy writes to the clipboard
// inside the click itself rather than after a network wait.
const cache = new Map();

function fetchText(url) {
  if (!cache.has(url)) {
    cache.set(
      url,
      fetch(url, { cache: 'no-store' }).then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status} for ${url}`);
        }

        return response.text();
      }),
    );
  }

  return cache.get(url).catch((error) => {
    cache.delete(url);
    throw error;
  });
}

async function copyWithFeedback(button, feedback, what, getText) {
  try {
    const text = await getText();
    const ok = await copyText(text);

    if (ok) {
      say(feedback, true, `Copied ${what} (${text.length} characters)`);
    } else {
      say(feedback, false, `Copy failed: the browser would not give this page the clipboard. Select the text and copy it by hand.`);
    }
  } catch (error) {
    say(feedback, false, `Copy failed: ${error.message}`);
  }

  button.focus();
}

// Command blocks: each button copies its own block and reports beside itself.
for (const button of document.querySelectorAll('[data-copy-from]')) {
  const feedback = button.parentElement.querySelector('.feedback');
  const source = document.getElementById(button.getAttribute('data-copy-from'));

  button.addEventListener('click', () => {
    copyWithFeedback(button, feedback, `the ${button.getAttribute('data-label')} command`, async () => source.textContent);
  });
}

// ── Step explorer ─────────────────────────────────────────────────────

const exampleButtons = [...document.querySelectorAll('[data-example-tab]')];

const exampleEls = [...document.querySelectorAll('.example')];

const pipelineEls = [...document.querySelectorAll('.pipeline')];

const stageEls = [...document.querySelectorAll('.stage')];

const position = document.getElementById('stage-position');

const prevButton = document.getElementById('stage-prev');

const nextButton = document.getElementById('stage-next');

const copyYamlButton = document.getElementById('copy-stage-yaml');

const copyOutputButton = document.getElementById('copy-stage-output');

const downloadLink = document.getElementById('download-stage-output');

const downloadJob = document.getElementById('download-job');

const state = { pipeline: pipelineEls[0].getAttribute('data-pipeline'), index: 0 };

function currentPipeline() {
  return pipelineEls.find((el) => el.getAttribute('data-pipeline') === state.pipeline);
}

function stagesOf(pipelineEl) {
  return stageEls.filter((el) => pipelineEl.contains(el));
}

function currentStage() {
  return stagesOf(currentPipeline())[state.index];
}

function render() {
  const pipelineEl = currentPipeline();
  const stages = stagesOf(pipelineEl);
  const exampleEl = pipelineEl.closest('.example');

  for (const el of exampleEls) {
    el.hidden = el !== exampleEl;
  }

  for (const button of exampleButtons) {
    button.setAttribute('aria-pressed', String(button.getAttribute('data-example-tab') === exampleEl.getAttribute('data-example')));
  }

  for (const el of pipelineEls) {
    el.hidden = el !== pipelineEl;
  }

  for (const button of document.querySelectorAll('[data-pipeline-tab]')) {
    button.setAttribute('aria-pressed', String(button.getAttribute('data-pipeline-tab') === state.pipeline));
  }

  stages.forEach((el, index) => {
    el.hidden = index !== state.index;

    if (index === state.index) {
      el.setAttribute('aria-current', 'step');
    } else {
      el.removeAttribute('aria-current');
    }
  });

  for (const step of pipelineEl.querySelectorAll('.step')) {
    const active = step.getAttribute('data-go') === stages[state.index].getAttribute('data-stage-id');

    if (active) {
      step.setAttribute('aria-current', 'step');
    } else {
      step.removeAttribute('aria-current');
    }
  }

  const label = stages[state.index].querySelector('.stage-h').textContent;

  position.textContent = label.split(':')[0];
  prevButton.disabled = state.index === 0;
  nextButton.disabled = state.index === stages.length - 1;
  downloadLink.setAttribute('href', stages[state.index].getAttribute('data-output-file'));
  downloadJob.setAttribute('href', pipelineEl.getAttribute('data-job-file'));
  fetchText(stages[state.index].getAttribute('data-output-file')).catch(() => null);
  history.replaceState(null, '', `#stage=${stages[state.index].getAttribute('data-stage-id')}`);
}

// Change the view without moving the page: paging must never jump the reader.
function go(pipelineId, index) {
  const x = window.scrollX;
  const y = window.scrollY;

  state.pipeline = pipelineId;
  state.index = index;
  render();
  window.scrollTo(x, y);
}

function step(delta) {
  const last = stagesOf(currentPipeline()).length - 1;

  go(state.pipeline, Math.min(Math.max(state.index + delta, 0), last));
}

for (const button of exampleButtons) {
  button.addEventListener('click', () => {
    const example = button.getAttribute('data-example-tab');
    const first = pipelineEls.find((el) => el.closest('.example').getAttribute('data-example') === example);

    go(first.getAttribute('data-pipeline'), 0);
  });
}

for (const button of document.querySelectorAll('[data-pipeline-tab]')) {
  button.addEventListener('click', () => go(button.getAttribute('data-pipeline-tab'), 0));
}

for (const button of document.querySelectorAll('.step')) {
  button.addEventListener('click', () => {
    const id = button.getAttribute('data-go');
    const index = stagesOf(currentPipeline()).findIndex((el) => el.getAttribute('data-stage-id') === id);

    go(state.pipeline, index);
  });
}

prevButton.addEventListener('click', () => step(-1));

nextButton.addEventListener('click', () => step(1));

document.addEventListener('keydown', (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
    return;
  }

  const target = event.target;
  const typing = target.closest('input, textarea, select, [contenteditable="true"]');

  if (typing || target.closest('pre')) {
    return;
  }

  if (event.key === 'ArrowRight') {
    step(1);
  } else if (event.key === 'ArrowLeft') {
    step(-1);
  } else if (event.key === 'Home') {
    go(state.pipeline, 0);
  } else if (event.key === 'End') {
    go(state.pipeline, stagesOf(currentPipeline()).length - 1);
  } else {
    return;
  }

  event.preventDefault();
});

copyYamlButton.addEventListener('click', () => {
  const feedback = document.getElementById('copy-yaml-status');

  copyWithFeedback(copyYamlButton, feedback, 'the stage configuration', async () => currentStage().querySelector('[data-yaml]').textContent);
});

copyOutputButton.addEventListener('click', () => {
  const feedback = document.getElementById('copy-output-status');

  copyWithFeedback(copyOutputButton, feedback, 'the stage output', () => fetchText(currentStage().getAttribute('data-output-file')));
});

async function downloadWithFeedback(url, feedback) {
  const name = url.split('/').pop();

  try {
    // Always a fresh request: a download reports what the server gives now.
    const response = await fetch(url, { cache: 'no-store' });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status} for ${url}`);
    }

    const text = await response.text();
    const blob = new Blob([text], { type: 'application/octet-stream' });
    const link = document.createElement('a');

    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
    say(feedback, true, `Downloaded ${name}`);
  } catch (error) {
    say(feedback, false, `Download failed: ${error.message}`);
  }
}

downloadLink.addEventListener('click', (event) => {
  event.preventDefault();
  downloadWithFeedback(downloadLink.getAttribute('href'), document.getElementById('download-output-status'));
});

// Job files: the link is a real URL; the click fetches it and reports beside the link.
for (const link of document.querySelectorAll('a[data-download]')) {
  link.addEventListener('click', (event) => {
    event.preventDefault();
    downloadWithFeedback(link.getAttribute('href'), link.parentElement.querySelector('.feedback'));
  });
}

// A stage can be linked: #stage=fuse-sign-record
const linked = /^#stage=(.+)$/.exec(location.hash);

if (linked) {
  const target = stageEls.find((el) => el.getAttribute('data-stage-id') === decodeURIComponent(linked[1]));

  if (target) {
    state.pipeline = target.getAttribute('data-pipeline');
    state.index = stagesOf(currentPipeline()).indexOf(target);
  }
}

render();
