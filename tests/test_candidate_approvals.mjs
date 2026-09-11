import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { approvalKey, acceptsState, requestBody, sameSnapshot, secureRequestId } from '../src/stock_watcher/server/static/approval-state.mjs';

const cardURL = process.env.STOCKWATCHER_APPROVAL_CARD_MODULE
  ? pathToFileURL(process.env.STOCKWATCHER_APPROVAL_CARD_MODULE)
  : new URL('../src/stock_watcher/server/static/candidate-card.js', import.meta.url);
const { candidateCardHTML, placeholderCardHTML } = await import(cardURL);
const candidate = { code: '300829.SZ', name: '金丹科技', rank: 1, price: 18.46, change_pct: 6.82, level: '强', is_formal: true };

test('daily scope is not code-only', () => {
  assert.notEqual(approvalKey('2026-09-11', candidate.code), approvalKey('2026-09-12', candidate.code));
});
test('old async responses cannot override a newer version', () => {
  assert.equal(acceptsState({ version: 2 }, { selected: true, version: 1 }), false);
  assert.equal(acceptsState({ version: 1 }, { selected: false, version: 2 }), true);
});
test('invalid state cannot masquerade as unchecked', () => {
  for (const incoming of [null, {}, { selected: 'true', version: 1 }, { selected: true, version: -1 }]) {
    assert.equal(acceptsState(null, incoming), false);
  }
});
test('request captures snapshot and selected explicitly, never toggles on server', () => {
  const body = requestBody(123, true, 0, 'a'.repeat(32));
  assert.equal(body.snapshot_id, 123);
  assert.equal(body.selected, true);
  assert.equal(body.expected_version, 0);
  assert.equal('user_id' in body, false);
});
test('bad request state fails before issuing a request', () => {
  assert.throws(() => requestBody(null, true, 0, 'a'.repeat(32)));
  assert.throws(() => requestBody(1, 'true', 0, 'a'.repeat(32)));
  assert.throws(() => requestBody(1, false, -1, 'a'.repeat(32)));
});
test('snapshot and account identity both have to match', () => {
  assert(sameSnapshot({ snapshot_id: 1, user_id: 2 }, 1, '2'));
  assert(!sameSnapshot({ snapshot_id: 2, user_id: 2 }, 1, '2'));
  assert(!sameSnapshot({ snapshot_id: 1, user_id: 3 }, 1, '2'));
});
test('feature off preserves baseline card and never adds a checkbox', () => {
  assert(!candidateCardHTML(candidate, { snapshot_id: 1 }).includes('data-approval-checkbox'));
});
test('enabled card has independently labelled native checkbox and original detail button', () => {
  const html = candidateCardHTML(candidate, { snapshot_id: 99 }, { approvalsEnabled: true });
  assert(html.includes('type="checkbox"'));
  assert(html.includes('data-approval-snapshot="99"'));
  assert(html.includes('data-detail="300829.SZ"'));
  assert(html.includes('data-approval-control'));
});
test('placeholder and invalid snapshots have no selectable feedback', () => {
  assert(!placeholderCardHTML(1).includes('data-approval-checkbox'));
  assert(!candidateCardHTML(candidate, { snapshot_id: null }, { approvalsEnabled: true }).includes('data-approval-checkbox'));
});
test('untrusted candidate text is escaped in feedback attributes', () => {
  const html = candidateCardHTML({ ...candidate, name: '\" onmouseover=\"evil()' }, { snapshot_id: 1 }, { approvalsEnabled: true });
  assert(!html.includes('name="" onmouseover'));
  assert(html.includes('&quot; onmouseover=&quot;evil()'));
});


test('secure request IDs use randomUUID when available', () => {
  assert.equal(secureRequestId({ randomUUID: () => 'f'.repeat(32) }), 'f'.repeat(32));
});
test('secure request IDs use CSPRNG bytes, never Math.random', () => {
  const id = secureRequestId({ getRandomValues: bytes => { bytes.fill(0xab); return bytes; } });
  assert.equal(id, 'ab'.repeat(16));
  assert.throws(() => secureRequestId({}));
});
