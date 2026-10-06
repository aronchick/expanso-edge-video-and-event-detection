// Live viewer: embeds go2rtc's own player for each camera and says plainly
// when go2rtc (port 1984 on this host) cannot be reached or lacks a stream.
(function () {
  const PORT = 1984;
  const PROBE_MS = 5000;

  const STREAMS = [
    { name: 'cam-outside', frame: 'north' },
    { name: 'cam-inside', frame: 'south' },
  ];

  const host = window.location.hostname || 'localhost';
  const scheme = window.location.protocol === 'https:' ? 'https:' : 'http:';
  const base = scheme + '//' + host + ':' + PORT;

  const app = { status: 'checking', streams: null, loaded: {} };

  function $(id) {
    return document.getElementById(id);
  }

  async function probe() {
    try {
      const response = await fetch(base + '/api/streams', { cache: 'no-store' });

      if (!response.ok) return { status: 'error', code: response.status, streams: null };

      return { status: 'up', code: 200, streams: await response.json() };
    } catch (error) {
      // A CORS refusal and a dead server both land here. A no-cors request
      // only fails when nothing is listening.
      try {
        await fetch(base + '/api/streams', { mode: 'no-cors', cache: 'no-store' });

        return { status: 'up', code: 0, streams: null };
      } catch (inner) {
        return { status: 'down', code: 0, streams: null };
      }
    }
  }

  function setNotice(kind, title, text) {
    const notice = $('source-notice');

    notice.dataset.kind = kind;
    $('source-notice-title').textContent = title;
    $('source-notice-text').textContent = text;
  }

  function setPill(kind, text) {
    const pill = $('live-pill');

    pill.dataset.kind = kind;
    pill.textContent = text;
  }

  function showPlaceholder(stream, title, detail) {
    const frame = $(stream.frame);
    const empty = $(stream.frame + '-empty');
    const head = document.createElement('span');
    const why = document.createElement('span');

    frame.hidden = true;
    empty.hidden = false;
    empty.textContent = '';
    head.textContent = title;
    why.className = 'dim';
    why.textContent = detail;
    empty.append(head, why);
    delete app.loaded[stream.name];
  }

  function showFrame(stream) {
    const frame = $(stream.frame);
    const url = base + '/stream.html?src=' + stream.name;

    if (app.loaded[stream.name] === url) return;

    frame.src = url;
    frame.hidden = false;
    $(stream.frame + '-empty').hidden = true;
    app.loaded[stream.name] = url;
  }

  function render(result) {
    const target = host + ':' + PORT;

    if (result.status === 'down') {
      setPill('err', 'Not reachable');
      setNotice('err', 'The video server is not reachable', 'Nothing answered at ' + target + '. Start the streams with just up, then press Check again.');
      STREAMS.forEach(function (stream) {
        showPlaceholder(stream, 'No video from ' + stream.name, 'go2rtc is not answering at ' + target + '.');
      });

      return;
    }

    if (result.status === 'error') {
      setPill('err', 'Server error');
      setNotice('err', 'The video server answered with an error', 'GET /api/streams returned ' + result.code + ' at ' + target + '.');
      STREAMS.forEach(function (stream) {
        showPlaceholder(stream, 'No video from ' + stream.name, 'go2rtc returned ' + result.code + '.');
      });

      return;
    }

    if (result.streams) {
      const missing = STREAMS.filter(function (stream) {
        return !Object.prototype.hasOwnProperty.call(result.streams, stream.name);
      });

      if (missing.length > 0) {
        setPill('warn', 'Stream missing');
        setNotice('warn', 'go2rtc is up but a stream is not configured', 'Missing: ' + missing.map(function (s) {
          return s.name;
        }).join(', ') + '. Configured: ' + (Object.keys(result.streams).join(', ') || 'none') + '.');
      } else {
        setPill('ok', 'Reachable');
        setNotice('ok', 'The video server is up', 'go2rtc at ' + target + ' lists both streams. If a picture stays black, check the camera.');
      }

      STREAMS.forEach(function (stream) {
        if (missing.includes(stream)) {
          showPlaceholder(stream, 'No stream named ' + stream.name, 'Add it to go2rtc.yaml and restart go2rtc.');
        } else {
          showFrame(stream);
        }
      });

      return;
    }

    setPill('ok', 'Reachable');
    setNotice('ok', 'The video server is up', 'go2rtc at ' + target + ' answered. Its stream list is not readable from this page, so a black picture means the camera is not feeding it.');
    STREAMS.forEach(showFrame);
  }

  async function check(manual) {
    const feedback = $('recheck-feedback');

    if (manual) {
      feedback.dataset.kind = '';
      feedback.textContent = 'Checking...';
    }

    app.status = 'checking';

    const result = await probe();

    render(result);

    if (manual) {
      feedback.dataset.kind = result.status === 'up' ? 'ok' : 'err';
      feedback.textContent = result.status === 'up'
        ? 'go2rtc answered.'
        : 'Still not reachable.';
    }
  }

  $('recheck-btn').addEventListener('click', function () {
    check(true);
  });

  check(false);
  window.setInterval(function () {
    check(false);
  }, PROBE_MS);
})();
