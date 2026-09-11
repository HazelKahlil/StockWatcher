/* Display size and compact watch layout. Presentation only; never fetch market data. */
(function (root) {
  'use strict';

  var SCALE_KEY = 'stockwatcher.ui.scale';
  var LAYOUT_KEY = 'stockwatcher.ui.watchLayout';
  var MIN_SCALE = 20;
  var MAX_SCALE = 150;
  var STEP = 5;
  var DEFAULT_SCALE = 100;

  function clampScale(value) {
    var number = typeof value === 'number' ? value : parseInt(String(value || ''), 10);
    if (!isFinite(number)) return DEFAULT_SCALE;
    var snapped = Math.round(number / STEP) * STEP;
    if (snapped < MIN_SCALE) return MIN_SCALE;
    if (snapped > MAX_SCALE) return MAX_SCALE;
    return snapped;
  }

  function normalizeLayout(value) {
    return value === 'compact' ? 'compact' : 'full';
  }

  function isDashboard(doc) {
    var node = doc || document;
    return Boolean(node.querySelector && node.querySelector('.dashboard-cards'));
  }

  function layoutForPage(pref, dashboard) {
    return pref === 'compact' && dashboard ? 'compact' : 'full';
  }

  function readStore(key) {
    try {
      return root.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function writeStore(key, value) {
    try {
      root.localStorage.setItem(key, value);
      return true;
    } catch (error) {
      return false;
    }
  }

  function readPrefs() {
    return {
      scale: clampScale(readStore(SCALE_KEY)),
      layout: normalizeLayout(readStore(LAYOUT_KEY)),
      persisted: true,
    };
  }

  function persistPrefs(scale, layout) {
    var scaleOk = writeStore(SCALE_KEY, String(clampScale(scale)));
    var layoutOk = writeStore(LAYOUT_KEY, normalizeLayout(layout));
    return scaleOk && layoutOk;
  }

  function applyToDocument(doc, scale, layoutPref) {
    var html = doc.documentElement;
    var safeScale = clampScale(scale);
    var factor = safeScale / 100;
    var dashboard = isDashboard(doc);
    var layout = layoutForPage(layoutPref, dashboard);
    html.style.setProperty('--ui-scale', String(factor));
    html.setAttribute('data-ui-scale', String(safeScale));
    html.setAttribute('data-watch-layout', layout);
    html.setAttribute('data-watch-pref', normalizeLayout(layoutPref));
    return { scale: safeScale, layout: layout, pref: normalizeLayout(layoutPref), dashboard: dashboard };
  }

  var state = {
    scale: DEFAULT_SCALE,
    layoutPref: 'full',
    persisted: true,
    open: false,
  };

  function current() {
    return applyToDocument(document, state.scale, state.layoutPref);
  }

  function syncControls() {
    var applied = current();
    var label = document.getElementById('display-scale-label');
    var readout = document.getElementById('display-scale-readout');
    var slider = document.getElementById('display-scale');
    var compact = document.getElementById('display-compact');
    var exitBtn = document.getElementById('watch-exit');
    var hint = document.getElementById('display-panel-hint');
    var text = applied.scale + '%';
    if (label) label.textContent = text;
    if (readout) readout.textContent = text;
    if (slider && String(slider.value) !== String(applied.scale)) slider.value = String(applied.scale);
    if (compact) {
      compact.hidden = !(applied.dashboard || applied.pref === 'compact');
      compact.setAttribute('aria-pressed', applied.pref === 'compact' ? 'true' : 'false');
      compact.textContent = applied.pref === 'compact' ? '正在仅看三只' : '仅看三只';
    }
    if (exitBtn) {
      exitBtn.hidden = applied.layout !== 'compact';
    }
    if (hint) {
      if (!state.persisted) {
        hint.textContent = '未能保存到此浏览器，当前调节仅在本页有效。仅看三只只影响当前观察页。';
      } else if (applied.pref === 'compact' && !applied.dashboard) {
        hint.textContent = '已记住仅看三只；回到当前观察后生效。其他页面保持完整内容。';
      } else {
        hint.textContent = '调整文字与布局大小，页面会根据窗口自动排列。仅看三只会收起次要内容。';
      }
    }
    return applied;
  }

  function setScale(nextScale, persist) {
    state.scale = clampScale(nextScale);
    if (persist !== false) state.persisted = persistPrefs(state.scale, state.layoutPref);
    syncControls();
  }

  function setLayoutPref(nextLayout, persist) {
    state.layoutPref = normalizeLayout(nextLayout);
    if (persist !== false) state.persisted = persistPrefs(state.scale, state.layoutPref);
    syncControls();
  }

  function setOpen(open) {
    var panel = document.getElementById('display-panel');
    var toggle = document.getElementById('display-toggle');
    if (!panel || !toggle) return;
    state.open = Boolean(open);
    panel.hidden = !state.open;
    toggle.setAttribute('aria-expanded', state.open ? 'true' : 'false');
    if (state.open) {
      var slider = document.getElementById('display-scale');
      if (slider) slider.focus({ preventScroll: true });
    } else if (document.activeElement && panel.contains(document.activeElement)) {
      toggle.focus({ preventScroll: true });
    }
  }

  function onDocumentPointer(event) {
    if (!state.open) return;
    var control = document.getElementById('display-control');
    if (control && !control.contains(event.target)) setOpen(false);
  }

  function onDocumentKey(event) {
    if (event.key === 'Escape' && state.open) {
      event.preventDefault();
      setOpen(false);
    }
  }

  function bind() {
    var toggle = document.getElementById('display-toggle');
    var panel = document.getElementById('display-panel');
    var slider = document.getElementById('display-scale');
    if (!toggle || !panel || !slider) return;
    slider.min = String(MIN_SCALE);
    slider.max = String(MAX_SCALE);
    slider.step = String(STEP);

    toggle.addEventListener('click', function () {
      setOpen(!state.open);
    });
    document.getElementById('display-panel-close')?.addEventListener('click', function () {
      setOpen(false);
    });
    slider.addEventListener('input', function () {
      setScale(slider.value, true);
    });
    document.querySelectorAll('[data-display-step]').forEach(function (button) {
      button.addEventListener('click', function () {
        setScale(state.scale + Number(button.getAttribute('data-display-step')), true);
      });
    });
    document.getElementById('display-reset')?.addEventListener('click', function () {
      setScale(DEFAULT_SCALE, true);
    });
    document.getElementById('display-compact')?.addEventListener('click', function () {
      setLayoutPref(state.layoutPref === 'compact' ? 'full' : 'compact', true);
    });
    document.getElementById('watch-exit')?.addEventListener('click', function () {
      setLayoutPref('full', true);
    });
    document.addEventListener('pointerdown', onDocumentPointer);
    document.addEventListener('keydown', onDocumentKey);
    syncControls();
  }

  function boot() {
    var prefs = readPrefs();
    state.scale = prefs.scale;
    state.layoutPref = prefs.layout;
    applyToDocument(document, state.scale, state.layoutPref);
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', bind, { once: true });
    } else {
      bind();
    }
  }

  var api = {
    SCALE_KEY: SCALE_KEY,
    LAYOUT_KEY: LAYOUT_KEY,
    MIN_SCALE: MIN_SCALE,
    MAX_SCALE: MAX_SCALE,
    STEP: STEP,
    DEFAULT_SCALE: DEFAULT_SCALE,
    clampScale: clampScale,
    normalizeLayout: normalizeLayout,
    layoutForPage: layoutForPage,
    isDashboard: isDashboard,
    readPrefs: readPrefs,
    persistPrefs: persistPrefs,
    applyToDocument: applyToDocument,
    setScale: setScale,
    setLayoutPref: setLayoutPref,
    boot: boot,
  };

  root.StockWatcherDisplay = api;
  if (typeof document !== 'undefined') boot();
})(typeof globalThis !== 'undefined' ? globalThis : this);
