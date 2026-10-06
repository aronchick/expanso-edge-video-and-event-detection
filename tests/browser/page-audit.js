// Rendered-page audit for the public bar (criterion 4).
//
// Evaluate this file in the page (agent-browser eval, Playwright evaluate,
// or devtools). It returns a JSON string with:
//   - the viewport width and document scroll widths (horizontal overflow)
//   - elements that stick out past the viewport and are not clipped by a
//     scroll container that itself fits
//   - every text node whose WCAG 2.x contrast ratio against its resolved
//     background is below AA (4.5, or 3 for large text)
//
// Backgrounds are resolved by compositing ancestor background colours.
// Text over a gradient or an image is flagged with gradient: true so a human
// can judge it; the ratio is then computed against the nearest solid colour.

(() => {
  const viewportWidth = document.documentElement.clientWidth;

  const parseColor = (value) => {
    const match = value.match(/rgba?\(([^)]+)\)/);

    if (!match) return null;

    const parts = match[1]
      .split(/[ ,/]+/)
      .filter(Boolean)
      .map(Number);

    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  };

  const compose = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });

  const channel = (value) => {
    const scaled = value / 255;

    return scaled <= 0.03928 ? scaled / 12.92 : Math.pow((scaled + 0.055) / 1.055, 2.4);
  };

  const luminance = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);

  const contrast = (a, b) => {
    const l1 = luminance(a);
    const l2 = luminance(b);

    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };

  const resolveBackground = (element) => {
    const layers = [];
    let gradient = false;
    let node = element;

    while (node && node.nodeType === 1) {
      const style = getComputedStyle(node);

      if (/gradient|url/.test(style.backgroundImage)) gradient = true;

      const color = parseColor(style.backgroundColor);

      if (color && color.a > 0) {
        layers.push(color);

        if (color.a >= 1) break;
      }

      node = node.parentElement;
    }

    let base = { r: 255, g: 255, b: 255, a: 1 };

    for (let i = layers.length - 1; i >= 0; i--) base = compose(layers[i], base);

    return { color: base, gradient };
  };

  const describe = (element) => {
    let text = element.tagName.toLowerCase();
    const classes = element.getAttribute('class');

    if (element.id) text += '#' + element.id;
    else if (classes) text += '.' + classes.trim().split(/\s+/).slice(0, 2).join('.');

    return text;
  };

  const effectiveOpacity = (element) => {
    let opacity = 1;

    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      opacity *= parseFloat(getComputedStyle(node).opacity);
    }

    return opacity;
  };

  const isHidden = (element) => {
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);

      if (style.display === 'none' || style.visibility === 'hidden') return true;

      if (parseFloat(style.opacity) === 0) return true;
    }

    return false;
  };

  const contrastFailures = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);

  for (let textNode = walker.nextNode(); textNode; textNode = walker.nextNode()) {
    const text = textNode.textContent.trim();
    const element = textNode.parentElement;

    if (!text || !element || isHidden(element)) continue;

    const box = element.getBoundingClientRect();

    if (box.width === 0 || box.height === 0) continue;

    const style = getComputedStyle(element);
    const key = describe(element) + '|' + style.color;

    if (seen.has(key)) continue;

    seen.add(key);

    const { color: background, gradient } = resolveBackground(element);
    let foreground = parseColor(style.color);

    if (!foreground) continue;

    const opacity = effectiveOpacity(element);

    foreground = compose({ ...foreground, a: foreground.a * opacity }, background);

    const size = parseFloat(style.fontSize);
    const bold = parseInt(style.fontWeight, 10) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const needed = large ? 3 : 4.5;
    const ratio = contrast(foreground, background);

    if (ratio < needed) {
      contrastFailures.push({
        element: describe(element),
        text: text.slice(0, 40),
        ratio: Number(ratio.toFixed(2)),
        needed,
        size,
        gradient,
        foreground: style.color,
        background: `rgb(${Math.round(background.r)},${Math.round(background.g)},${Math.round(background.b)})`,
      });
    }
  }

  const clippedByFittingParent = (element) => {
    for (let node = element.parentElement; node && node !== document.body; node = node.parentElement) {
      const overflow = getComputedStyle(node).overflowX;

      if (overflow === 'hidden' || overflow === 'auto' || overflow === 'scroll') {
        const parentBox = node.getBoundingClientRect();

        if (parentBox.right <= viewportWidth + 1 && parentBox.left >= -1) return true;
      }
    }

    return false;
  };

  const sticksOut = [];

  for (const element of document.querySelectorAll('body *')) {
    const box = element.getBoundingClientRect();

    if (box.width === 0) continue;

    if ((box.right > viewportWidth + 1 || box.left < -1) && !clippedByFittingParent(element)) {
      sticksOut.push({
        element: describe(element),
        left: Math.round(box.left),
        right: Math.round(box.right),
      });
    }
  }

  const root = document.documentElement;

  return JSON.stringify(
    {
      viewportWidth,
      documentScrollWidth: root.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      bodyOverflowX: getComputedStyle(document.body).overflowX,
      theme: root.getAttribute('data-theme'),
      contrastFailureCount: contrastFailures.length,
      contrastFailures: contrastFailures.slice(0, 40),
      stickingOutCount: sticksOut.length,
      stickingOut: sticksOut.slice(0, 25),
    },
    null,
    2,
  );
})();
