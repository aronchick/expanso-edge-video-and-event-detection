// Theme toggle shared by every box-counting page. Light is the default; the
// OS colour-scheme preference is ignored. The choice is kept in localStorage
// when it is available and the page works without it.
(function () {
  const KEY = 'box-monitor-theme';
  const root = document.documentElement;

  function readStored() {
    try {
      return window.localStorage.getItem(KEY);
    } catch (error) {
      return null;
    }
  }

  function store(value) {
    try {
      window.localStorage.setItem(KEY, value);
    } catch (error) {
      // Storage is blocked; the toggle still works for this page view.
    }
  }

  function currentTheme() {
    return root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  function paintButtons() {
    const dark = currentTheme() === 'dark';
    const buttons = document.querySelectorAll('.theme-toggle');

    buttons.forEach(function (button) {
      const state = button.querySelector('.state');

      button.setAttribute('aria-pressed', dark ? 'true' : 'false');

      if (state) state.textContent = dark ? 'on' : 'off';
    });
  }

  function apply(theme) {
    root.setAttribute('data-theme', theme);
    paintButtons();
  }

  apply(readStored() === 'dark' ? 'dark' : 'light');

  document.addEventListener('DOMContentLoaded', function () {
    paintButtons();

    document.querySelectorAll('.theme-toggle').forEach(function (button) {
      button.addEventListener('click', function () {
        const next = currentTheme() === 'dark' ? 'light' : 'dark';

        apply(next);
        store(next);
      });
    });
  });
})();
