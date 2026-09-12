import { approvalKey, acceptsState, requestBody, sameSnapshot, secureRequestId, shouldReadApprovalState, isCurrentApprovalLoad } from './approval-state.mjs?v=3';

// apiJson is the existing app.js CSRF-aware transport; this module does not replace auth.
export function createApprovalController({ cards, apiJson, userId, status, history, pendingRoot }) {
  const enabled = cards?.dataset.approvalsEnabled === 'true';
  const cache = new Map();
  const pending = new Map();
  let current = null;
  let shownSnapshotId = 0;
  let loadedSnapshotId = 0;
  let loadFailed = false;
  let failedSnapshotId = 0;
  let loadInFlight = false;
  let loadRetryTimer = null;
  let loadAttempts = 0;
  let loadEpoch = 0;
  let loadController = null;
  let accountInvalid = false;
  let disposed = false;
  let historyCursor = null;
  let historyLoaded = false;
  let historyBusy = false;

  const announce = (message) => { if (status) status.textContent = message; };
  const controls = () => [...cards.querySelectorAll('[data-approval-control]')];
  const itemKey = (control) => {
    if (!shownSnapshotId || Number(control.dataset.approvalSnapshot) !== shownSnapshotId) {
      return null;
    }
    const day = current?.trade_date || '';
    if (!day) return null;
    return approvalKey(day, control.dataset.approvalCode);
  };

  const reloadButton = () => document.getElementById('approval-reload');

  function invalidateLoad() {
    loadEpoch += 1;
    loadController?.abort();
    loadController = null;
    if (loadRetryTimer) {
      clearTimeout(loadRetryTimer);
      loadRetryTimer = null;
    }
    loadFailed = false;
    failedSnapshotId = 0;
    loadAttempts = 0;
    loadInFlight = false;
  }

  function scheduleLoadRetry(snapshotId) {
    if (loadRetryTimer || loadAttempts >= 3 || disposed) return;
    const scheduledFor = snapshotId;
    loadRetryTimer = setTimeout(() => {
      loadRetryTimer = null;
      if (!disposed && shownSnapshotId === scheduledFor && loadedSnapshotId !== scheduledFor) {
        void beginLoad(scheduledFor);
      }
    }, 1500 * Math.max(1, loadAttempts));
  }

  function beginLoad(snapshotId) {
    if (disposed || !Number.isSafeInteger(snapshotId) || snapshotId <= 0) return;
    loadEpoch += 1;
    const myEpoch = loadEpoch;
    loadController?.abort();
    loadController = new AbortController();
    loadInFlight = true;
    return readState(snapshotId, myEpoch, loadController);
  }

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

  function write(el, name, value) {
    if (el.getAttribute(name) === value) return;
    el.setAttribute(name, value);
  }

  function paint() {
    if (!enabled || disposed) return;
    paintPending();
    const reload = reloadButton();
    if (reload) reload.hidden = !loadFailed || accountInvalid;
    const loaded = loadedSnapshotId === shownSnapshotId && !loadFailed;
    for (const control of controls()) {
      const input = control.querySelector('input');
      const label = control.querySelector('[data-approval-label]');
      const retry = control.querySelector('[data-approval-retry]');
      const key = itemKey(control);
      const state = key && cache.get(key);
      const work = key && pending.get(key);
      const known = Boolean(state) || Boolean(work);
      if (label.textContent !== '选择') label.textContent = '选择';
      if (!known) {
        if (!input.disabled) input.disabled = true;
        if (control.dataset.approvalReady !== 'false') control.dataset.approvalReady = 'false';
        write(control, 'aria-busy', String(loadInFlight));
        const syncTitle = loadFailed
          ? '个人选择暂不可用，可稍后重新读取。'
          : '正在同步个人选择。';
        if (control.title !== syncTitle) control.title = syncTitle;
        continue;
      }
      const selected = work ? work.body.selected : Boolean(state?.selected);
      const ready = loaded && Boolean(state) && !accountInvalid;
      const failed = Boolean(work?.failed);
      const busy = Boolean(work && !work.failed);
      if (input.checked !== selected) input.checked = selected;
      if (input.disabled !== (!ready || Boolean(work))) input.disabled = !ready || Boolean(work);
      if (control.dataset.approvalReady !== String(ready)) {
        control.dataset.approvalReady = String(ready);
      }
      if (control.dataset.approvalPending !== String(failed)) {
        control.dataset.approvalPending = String(failed);
      }
      if (control.dataset.approvalSelected !== String(selected)) {
        control.dataset.approvalSelected = String(selected);
      }
      retry.hidden = !failed;
      write(control, 'aria-busy', String(busy));
      const day = current?.trade_date || '';
      write(input, 'aria-label', `${control.dataset.approvalName}，${day}候选，选择`);
      const nextTitle = failed
        ? '保存状态未确认。重试会复用同一请求，不重复计数。'
        : selected
          ? `已选择 ${day || '此批'} 候选。再次点击可取消。`
          : `未选择。点击选择 ${day || '此批'} 候选。`;
      if (control.title !== nextTitle) control.title = nextTitle;
    }
  }

  function accept(incoming) {
    const key = approvalKey(incoming.trade_date, incoming.code);
    if (acceptsState(cache.get(key), incoming)) cache.set(key, incoming);
  }

  async function readState(snapshotId, myEpoch, controller) {
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await apiJson(
        `/api/v1/me/candidate-approvals/state?snapshot_id=${snapshotId}`,
        { signal: controller.signal, cache: 'no-store' },
      );
      if (disposed || !isCurrentApprovalLoad(myEpoch, loadEpoch, snapshotId, shownSnapshotId)) {
        return;
      }
      if (!sameSnapshot(response, snapshotId, userId)) {
        accountInvalid = true;
        cache.clear();
        announce('账户或候选已变化，请重新加载页面后操作。');
        return;
      }
      current = response;
      loadedSnapshotId = snapshotId;
      loadFailed = false;
      failedSnapshotId = 0;
      loadAttempts = 0;
      for (const state of response.items) accept(state);
      if (status?.textContent.includes('反馈暂不可用') || status?.textContent.includes('重新读取')) {
        announce('');
      }
    } catch (error) {
      if (disposed || !isCurrentApprovalLoad(myEpoch, loadEpoch, snapshotId, shownSnapshotId)) {
        return;
      }
      if (error?.name === 'AbortError') return;
      if (error.status === 401) accountInvalid = true;
      loadFailed = true;
      failedSnapshotId = snapshotId;
      loadAttempts += 1;
      announce('反馈暂不可用，候选观察不受影响。可点击重新读取。');
      scheduleLoadRetry(snapshotId);
    } finally {
      clearTimeout(timer);
      if (isCurrentApprovalLoad(myEpoch, loadEpoch, snapshotId, shownSnapshotId)) {
        loadInFlight = false;
        if (!disposed) paint();
      }
    }
  }

  async function submit(key) {
    const work = pending.get(key);
    if (!work || work.inFlight || accountInvalid || disposed) return;
    work.inFlight = true;
    work.failed = false;
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
        if (shownSnapshotId) await beginLoad(shownSnapshotId);
        announce('反馈版本已变化，请核对当前状态后重新选择。');
      } else if ([400, 403, 404, 422].includes(error.status)) {
        pending.delete(key);
        if (shownSnapshotId) await beginLoad(shownSnapshotId);
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
      paint();
      void submit(key);
    } catch {
      announce('此浏览器暂不支持安全保存反馈，请使用受支持的浏览器。');
      paint();
    }
  }

  function onClick(event) {
    const retry = event.target.closest('[data-approval-retry]');
    if (retry && cards.contains(retry)) {
      const key = itemKey(retry.closest('[data-approval-control]'));
      if (key) void submit(key);
      return;
    }
    if (event.target.closest('#approval-reload')) {
      loadAttempts = 0;
      loadFailed = false;
      failedSnapshotId = 0;
      if (shownSnapshotId) void beginLoad(shownSnapshotId);
    }
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
  const onVisible = () => {
    if (document.hidden || !shownSnapshotId) return;
    loadAttempts = 0;
    loadFailed = false;
    failedSnapshotId = 0;
    void beginLoad(shownSnapshotId);
  };
  if (enabled) {
    cards.addEventListener('change', onChange);
    cards.addEventListener('click', onClick);
    reloadButton()?.addEventListener('click', onClick);
    history?.addEventListener('toggle', onToggle);
    history?.querySelector('[data-approval-history-more]')?.addEventListener('click', onMore);
    document.addEventListener('visibilitychange', onVisible);
  }
  return {
    enabled,
    render(state) {
      if (!enabled || disposed) return;
      const id = Number(state?.snapshot_id);
      if (!Number.isSafeInteger(id) || id <= 0) {
        shownSnapshotId = 0;
        if (current !== null) {
          current = null;
          paint();
        }
        return;
      }
      const snapshotChanged = shownSnapshotId !== id;
      shownSnapshotId = id;
      if (snapshotChanged) {
        invalidateLoad();
        current = {
          snapshot_id: id,
          trade_date: state.trade_date || '',
          user_id: userId,
          items: [],
        };
      }
      paint();
      if (shouldReadApprovalState(
        shownSnapshotId, loadedSnapshotId, loadInFlight, failedSnapshotId,
      )) {
        void beginLoad(id);
      }
    },
    dispose() {
      disposed = true;
      invalidateLoad();
      for (const work of pending.values()) work.controller?.abort();
      cards?.removeEventListener('change', onChange);
      cards?.removeEventListener('click', onClick);
      reloadButton()?.removeEventListener('click', onClick);
      history?.removeEventListener('toggle', onToggle);
      history?.querySelector('[data-approval-history-more]')?.removeEventListener('click', onMore);
      document.removeEventListener('visibilitychange', onVisible);
    },
  };
}
