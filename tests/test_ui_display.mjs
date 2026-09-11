import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

const displaySource = readFileSync(new URL('../src/stock_watcher/server/static/display.js', import.meta.url), 'utf8');
const displayCss = readFileSync(new URL('../src/stock_watcher/server/static/display.css', import.meta.url), 'utf8');
const baseHtml = readFileSync(new URL('../src/stock_watcher/server/templates/base.html', import.meta.url), 'utf8');

function loadDisplay({dashboard = false, store = {}, failStore = false} = {}) {
  const html = {
    style: {
      props: {},
      setProperty(name, value) { this.props[name] = value; },
    },
    attrs: {},
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name]; },
  };
  const document = {
    documentElement: html,
    readyState: 'complete',
    querySelector(selector) {
      return dashboard && selector === '.dashboard-cards' ? {className: 'dashboard-cards'} : null;
    },
    querySelectorAll() { return []; },
    getElementById() { return null; },
    addEventListener() {},
  };
  const context = {
    document,
    localStorage: {
      getItem(key) { return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null; },
      setItem(key, value) {
        if (failStore) throw new Error('quota');
        store[key] = String(value);
      },
    },
  };
  context.globalThis = context;
  vm.runInNewContext(displaySource, context);
  return {api: context.StockWatcherDisplay, html, store};
}

test('display entry is labeled and lives next to the account cluster', () => {
  assert.match(baseHtml, /class="account-cluster"/);
  assert.match(baseHtml, /显示大小/);
  assert.match(baseHtml, /id="display-scale"/);
  assert.match(baseHtml, /仅看三只/);
  assert.match(baseHtml, /display\.js\?v=4/);
  assert.match(baseHtml, /display\.css\?v=4/);
  assert.match(baseHtml, /调整文字与布局大小/);
  assert.match(baseHtml, /min="20"/);
  assert.match(baseHtml, /max="150"/);
});

test('scale clamps to readable steps and ignores garbage', () => {
  const {api} = loadDisplay();
  assert.equal(api.MIN_SCALE, 20);
  assert.equal(api.MAX_SCALE, 150);
  assert.equal(api.clampScale(100), 100);
  assert.equal(api.clampScale('87'), 85);
  assert.equal(api.clampScale(10), 20);
  assert.equal(api.clampScale(20), 20);
  assert.equal(api.clampScale(150), 150);
  assert.equal(api.clampScale(200), 150);
  assert.equal(api.clampScale('nope'), 100);
  assert.equal(api.clampScale(undefined), 100);
});

test('compact layout is independent from scale and only applies on the observation page', () => {
  const {api} = loadDisplay();
  assert.equal(api.normalizeLayout('compact'), 'compact');
  assert.equal(api.normalizeLayout('full'), 'full');
  assert.equal(api.normalizeLayout('weird'), 'full');
  assert.equal(api.layoutForPage('compact', true), 'compact');
  assert.equal(api.layoutForPage('compact', false), 'full');
  assert.equal(api.layoutForPage('full', true), 'full');
});

test('compact CSS cannot hide history or review pages', () => {
  assert.match(displayCss, /html\[data-watch-layout="compact"\]:has\(\.dashboard-cards\)/);
  assert.match(displayCss, /container-type: inline-size/);
  assert.doesNotMatch(displayCss, /100svw \/ var\(--ui-scale\)/);
  assert.doesNotMatch(displayCss, /max\(16rem, calc\(var\(--ui-scale\)/);
  assert.match(displayCss, /--ui-name-size/);
  assert.match(displayCss, /--ui-page-pad-x/);
  assert.doesNotMatch(displayCss, /html\[data-watch-layout="compact"\] \.page-heading/);
  assert.doesNotMatch(displayCss, /html\[data-watch-layout="compact"\] \.outcome-page/);
});

test('saved compact preference does not hide non-dashboard content', () => {
  const {api, html} = loadDisplay({
    dashboard: false,
    store: {'stockwatcher.ui.scale': '80', 'stockwatcher.ui.watchLayout': 'compact'},
  });
  const applied = api.applyToDocument(
    {documentElement: html, querySelector: () => null},
    80,
    'compact',
  );
  assert.equal(applied.layout, 'full');
  assert.equal(applied.pref, 'compact');
  assert.equal(html.attrs['data-watch-layout'], 'full');
  assert.equal(html.attrs['data-ui-scale'], '80');
});

test('compact on the observation page keeps a distinct layout flag', () => {
  const {api, html} = loadDisplay({dashboard: true});
  const applied = api.applyToDocument(
    {documentElement: html, querySelector: (sel) => sel === '.dashboard-cards' ? {} : null},
    100,
    'compact',
  );
  assert.equal(applied.layout, 'compact');
  assert.equal(html.attrs['data-watch-layout'], 'compact');
  assert.equal(html.style.props['--ui-scale'], '1');
});

test('storage failure still applies the chosen size in memory', () => {
  const {api, html} = loadDisplay({failStore: true, dashboard: true});
  assert.equal(api.persistPrefs(90, 'full'), false);
  const applied = api.applyToDocument(
    {documentElement: html, querySelector: (sel) => sel === '.dashboard-cards' ? {} : null},
    90,
    'full',
  );
  assert.equal(applied.scale, 90);
  assert.equal(html.style.props['--ui-scale'], '0.9');
});

test('candidate cards keep identity, quote and meta as groups', () => {
  const cardSource = readFileSync(new URL('../src/stock_watcher/server/static/candidate-card.js', import.meta.url), 'utf8');
  assert.match(cardSource, /class="candidate-primary"/);
  assert.match(cardSource, /class="candidate-quote"/);
  assert.match(cardSource, /class="candidate-aux"/);
  assert.match(cardSource, /class="level-tag/);
  const dashHtml = readFileSync(new URL('../src/stock_watcher/server/templates/dashboard.html', import.meta.url), 'utf8');
  assert.match(dashHtml, /class="candidate-primary"/);
  assert.match(dashHtml, /class="candidate-aux"/);
  const dash = readFileSync(new URL('../src/stock_watcher/server/static/dashboard.js', import.meta.url), 'utf8');
  assert.match(dash, /candidate-card\.js/);
});

test('display script never talks to market APIs', () => {
  assert.doesNotMatch(displaySource, /\bfetch\s*\(/);
  assert.doesNotMatch(displaySource, /WebSocket/);
  assert.doesNotMatch(displaySource, /manual-refresh/);
  assert.doesNotMatch(displaySource, /\/api\/v1\//);
});
