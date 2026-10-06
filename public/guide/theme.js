// Applies the saved theme before first paint so there is no flash.
// Light is the default. The operating-system color-scheme preference is
// ignored on purpose: the dark theme is an explicit choice made with the
// toggle in the header.
(function applySavedTheme() {
  let saved = null;

  try {
    saved = localStorage.getItem('edge-isr-theme');
  } catch (_e) {
    saved = null;
  }

  document.documentElement.setAttribute('data-theme', saved === 'dark' ? 'dark' : 'light');
})();
