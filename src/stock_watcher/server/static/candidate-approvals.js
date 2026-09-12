import { approvalKey, acceptsState, requestBody, sameSnapshot, secureRequestId } from './approval-state.mjs?v=1';

// apiJson is the existing app.js CSRF-aware transport; this module does not replace auth.
export function createApprovalController({ cards, apiJson, userId, status, history, pendingRoot }) {
  const enabled = cards?.dataset.approvalsEnabled === 'true';
  const cache = new Map();
  const pending = new Map();
  let current = null;
  let epoch = 0;
  let loadController = null;
  let accountInvalid = false;
  let disposed = false;
  let historyCursor = null;
  let historyLoaded = false;
  let historyBusy = false;

  const announce = (message) => { if (status) status.textContent = message; };
  const controls = () => [...cards.querySelectorAll('[data-approval-control]')];
  const itemKey = (control) => {
    if (!current || Number(control.dataset.approvalSnapshot) !== current.snapshot_id) return null;
    return approvalKey(current.trade_date, control.dataset.approvalCode);
  };

  function paintPending() {
    if (!pendingRoot) return;
    const active = new Set();
    for (const [key, work] of pending) {
      if (!work.failed) continue;
      active.add(work.body.request_id);
      let row = pendingRoot.querySelector(`[data-pending-request="${work.body.request_id}"]`);
      if (!row) {
        row = document.createElement('p');
        row.dataset.pendingRequest = work.body.request_id;
        const text = document.createElement('span');
        text.textContent = `${work.code} 的上次反馈尚未确认。 `;
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.textContent = '重试保存';
        retry.addEventListener('click', () => { void submit(key); });
        row.append(text, retry);
        pendingRoot.append(row);
      }
      row.querySelector('button').disabled = accountInvalid || work.inFlight;
    }
    for (const row of [...pendingRoot.children]) {
      if (!active.has(row.dataset.pendingRequest)) row.remove();
    }
  }

  function paint() {
    if (!enabled || disposed) return;
    paintPending();
    for (const control of controls()) {
      const input = control.querySelector('input');
      const label = control.querySelector('[data-approval-label]');
      const retry = control.querySelector('[data-approval-retry]');
      const key = itemKey(control);
      const state = key && cache.get(key);
      const work = key && pending.get(key);
      const selected = work ? work.body.selected : Boolean(state?.selected);
      input.checked = selected;
      input.disabled = accountInvalid || !state || Boolean(work);
      control.dataset.approvalPending = String(Boolean(work));
      control.dataset.approvalSelected = String(selected);
      label.textContent = '选择';
      retry.hidden = !work?.failed || accountInvalid;
      control.setAttribute('aria-busy', String(Boolean(work && !work.failed)));
      const day = current?.trade_date || '';
      input.setAttribute('aria-label', `${control.dataset.approvalName}，${day}候选，选择`);
      control.title = work?.failed ? '保存状态未确认。重试会复用同一请求，不重复计数。'
        : `针对 ${day || '此批'} 候选的个人选择；未选表示未反馈。`;
    }
  }

  function accept(incoming) {
    const key = approvalKey(incoming.trade_date, incoming.code);
    if (acceptsState(cache.get(key), incoming)) cache.set(key, incoming);
  }

  async function readState(snapshotId) {
    const myEpoch = ++epoch;
    loadController?.abort();
    loadController = new AbortController();
    const controller = loadController;
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await apiJson(
        `/api/v1/me/candidate-approvals/state?snapshot_id=${snapshotId}`,
        { signal: controller.signal, cache: 'no-store' },
      );
      if (disposed || myEpoch !== epoch) return;
      if (!sameSnapshot(response, snapshotId, userId)) {
        accountInvalid = true;
        cache.clear();
        announce('账户或候选已变化，请重新加载页面后操作。');
        return;
      }
      current = response;
      for (const state of response.items) accept(state);
      announce('');
    } catch (error) {
      if (disposed || myEpoch !== epoch) return;
      if (error.status === 401) accountInvalid = true;
      announce('反馈暂不可用，候选观察不受影响；页面同步后可重试。');
    } finally {
      clearTimeout(timer);
      if (myEpoch === epoch) paint();
    }
  }

  async function submit(key) {
    const work = pending.get(key);
    if (!work || work.inFlight || accountInvalid || disposed) return;
    work.inFlight = true;
    work.failed = false;
    paint();
    const controller = new AbortController();
    work.controller = controller;
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const result = await apiJson(
        `/api/v1/me/candidate-approvals/${encodeURIComponent(work.code)}`,
        { method: 'PUT', body: JSON.stringify(work.body), signal: controller.signal },
      );
      if (disposed) return;
      if (String(result.user_id) !== String(userId)) {
        accountInvalid = true;
        announce('账户已变化，请重新登录后核对反馈。');
        return;
      }
      // The response contains CURRENT state, not just the potentially old receipt.
      accept(result.state);
      pending.delete(key);
      historyLoaded = false;
      const visible = current && key === approvalKey(current.trade_date, work.code);
      announce(visible ? (result.state.selected ? '选择已保存。' : '选择已取消。')
        : '上一批候选的反馈已保存，当前候选未受影响。');
    } catch (error) {
      if (disposed) return;
      if (error.status === 409) {
        pending.delete(key);
        cache.delete(key);
        if (current) await readState(current.snapshot_id);
        announce('反馈版本已变化，请核对当前状态后重新选择。');
      } else if ([400, 403, 404, 422].includes(error.status)) {
        pending.delete(key);
        if (current) await readState(current.snapshot_id);
        announce('此次反馈未保存，请刷新页面并核对候选或登录状态。');
      } else {
        // A timeout can occur AFTER commit. Keep the same UUID/body for retry.
        work.failed = true;
        if (error.status === 401) accountInvalid = true;
        announce('保存状态未确认。请使用反馈区的重试，避免重复操作。');
      }
    } finally {
      clearTimeout(timer);
      work.inFlight = false;
      paint();
    }
  }

  function onChange(event) {
    const input = event.target.closest('input[data-approval-checkbox]');
    if (!input || !cards.contains(input)) return;
    const control = input.closest('[data-approval-control]');
    const key = itemKey(control);
    const known = key && cache.get(key);
    if (!key || !known || pending.has(key) || accountInvalid) { paint(); return; }
    try {
      const body = requestBody(
        Number(control.dataset.approvalSnapshot), input.checked,
        known.version, secureRequestId(),
      );
      pending.set(key, { body, code: control.dataset.approvalCode, failed: false, inFlight: false });
      void submit(key);
    } catch {
      announce('此浏览器暂不支持安全保存反馈，请使用受支持的浏览器。');
      paint();
    }
  }

  function onClick(event) {
    const retry = event.target.closest('[data-approval-retry]');
    if (!retry || !cards.contains(retry)) return;
    const key = itemKey(retry.closest('[data-approval-control]'));
    if (key) void submit(key);
  }

  async function loadHistory(reset = false) {
    if (!history || historyBusy) return;
    historyBusy = true;
    const list = history.querySelector('[data-approval-history-list]');
    const more = history.querySelector('[data-approval-history-more]');
    const note = history.querySelector('[data-approval-history-status]');
    more.disabled = true;
    try {
      const cursor = !reset && historyCursor ? `&cursor=${historyCursor}` : '';
      const response = await apiJson(`/api/v1/me/candidate-approvals/events?limit=20${cursor}`);
      if (disposed) return;
      if (String(response.user_id) !== String(userId)) {
        accountInvalid = true;
        cache.clear();
        list.replaceChildren();
        note.textContent = '账户已变化，请重新加载页面。';
        paint();
        return;
      }
      if (reset) list.replaceChildren();
      for (const item of response.items) {
        const row = document.createElement('li');
        const name = item.context?.candidate?.name || item.code;
        const verb = item.action === 'approve' ? '选择' : '取消选择';
        const when = new Date(item.recorded_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
        row.textContent = `${when} · ${verb} ${name}（${item.code}）· 候选日 ${item.trade_date}`;
        list.append(row);
      }
      historyCursor = response.next_cursor;
      more.hidden = !historyCursor;
      note.textContent = list.children.length ? '仅显示你的操作记录，撤销不代表否定。' : '尚无反馈记录。';
      historyLoaded = true;
    } catch {
      note.textContent = '记录暂不可用，请关闭后重新打开重试。';
    } finally {
      more.disabled = false;
      historyBusy = false;
    }
  }

  const onToggle = () => { if (history.open && !historyLoaded) void loadHistory(true); };
  const onMore = () => { void loadHistory(false); };
  const onFocus = () => { if (current && !document.hidden) void readState(current.snapshot_id); };
  if (enabled) {
    cards.addEventListener('change', onChange);
    cards.addEventListener('click', onClick);
    history?.addEventListener('toggle', onToggle);
    history?.querySelector('[data-approval-history-more]')?.addEventListener('click', onMore);
    window.addEventListener('focus', onFocus);
  }
  return {
    enabled,
    render(state) {
      if (!enabled || disposed) return;
      const id = Number(state?.snapshot_id);
      if (!Number.isSafeInteger(id) || id <= 0) { current = null; paint(); return; }
      if (current?.snapshot_id !== id) current = null;
      paint(); // Reapply checked DOM properties after dashboard.patchElement.
      void readState(id);
    },
    dispose() {
      disposed = true;
      ++epoch;
      loadController?.abort();
      for (const work of pending.values()) work.controller?.abort();
      cards?.removeEventListener('change', onChange);
      cards?.removeEventListener('click', onClick);
      history?.removeEventListener('toggle', onToggle);
      history?.querySelector('[data-approval-history-more]')?.removeEventListener('click', onMore);
      window.removeEventListener('focus', onFocus);
    },
  };
}
