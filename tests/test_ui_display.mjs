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
  assert.match(baseHtml, /display\.js\?v=1/);
  assert.match(baseHtml, /display\.css\?v=1/);
});

test('scale clamps to readable steps and ignores garbage', () => {
  const {api} = loadDisplay();
  assert.equal(api.clampScale(100), 100);
  assert.equal(api.clampScale('87'), 85);
  assert.equal(api.clampScale(10), 75);
  assert.equal(api.clampScale(200), 125);
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
  const applied = api.applyToDocument(
    {documentElement: html, querySelector: (sel) => sel === '.dashboard-cards' ? {} : null},
    90,
    'full',
  );
  assert.equal(applied.scale, 90);
  assert.equal(html.style.props['--ui-scale'], '0.9');
});

test('display script never talks to market APIs', () => {
  assert.doesNotMatch(displaySource, /\bfetch\s*\(/);
  assert.doesNotMatch(displaySource, /WebSocket/);
  assert.doesNotMatch(displaySource, /manual-refresh/);
  assert.doesNotMatch(displaySource, /\/api\/v1\//);
});
