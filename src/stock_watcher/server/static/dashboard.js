import { api, apiJson, connectEvents, esc, fmtTime, onEvent, requestNotificationPermission, notify } from './app.js?v=8';
import { enter, enhanceDetails, openDrawer, closeDrawer, patchElement } from './motion.js?v=1';
import { candidateTimestamp, retainedCandidates, displayMarketPhase } from './presentation.js?v=1';
import { candidateCardHTML, placeholderCardHTML, levelMeta } from './candidate-card.js?v=6-switch';
import { createApprovalController } from './candidate-approvals.js?v=4';
let approvalController = null;

const stateLabels = { starting: '启动中', warming: '预热', healthy: '正常', stale: '陈旧', stopped: '停止' };
const refreshStages = [
  { maxSeconds: 2, label: '连接行情数据' },
  { maxSeconds: 6, label: '扫描全市场候选' },
  { maxSeconds: Infinity, label: '整理实时 Top3' },
];
const refreshCommandWaitMs = 310000;
const refreshFailureLabels = {
  timeout: '刷新超时：没有产生新候选',
  worker_watchdog_timeout: 'Worker 已自恢复：本次没有产生新候选',
  credential_missing: 'Token 未配置，未产生新候选',
  'credential-missing': 'Token 未配置，未产生新候选',
  rate_limited: '行情接口限流，未产生新候选',
  'universe-refresh': '基础行情缓存暂不可用，未产生新候选',
};
const taskLabels = {'scheduled-09:45': '09:45 观察', 'scheduled-14:45': '14:45 观察', 'daily-summary': '盘后总结'};
const taskStates = {pending:'待执行', running:'进行中', succeeded:'已完成', failed:'未完成', missed:'已错过', blocked:'暂不可用', retrying:'重试中'};
let refreshProgressTimer = null;
let refreshProgressHideTimer = null;
const handledAlertIds = new Set();
const automaticAlertQueue = [];
let activeAutomaticAlert = null;
let latestDashboardState = null;
let receivedAlertSequence = 0;
let dismissedThroughAlertSequence = 0;
let stateRefreshPromise = null;
let stateRefreshQueued = false;

function clearRefreshProgressTimers() {
  if (refreshProgressTimer) {
    clearInterval(refreshProgressTimer);
    refreshProgressTimer = null;
  }
  if (refreshProgressHideTimer) {
    clearTimeout(refreshProgressHideTimer);
    refreshProgressHideTimer = null;
  }
}

function updateRefreshProgress(startedAt, state = 'working', labelOverride = '') {
  const progress = document.getElementById('refresh-progress');
  if (!progress) return;
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  const stage = refreshStages.find((item) => elapsedSeconds < item.maxSeconds) || refreshStages.at(-1);
  progress.hidden = false;
  progress.dataset.state = state;
  const label = document.getElementById('refresh-progress-label');
  const elapsed = document.getElementById('refresh-progress-elapsed');
  if (label) label.textContent = labelOverride || stage.label;
  if (elapsed) elapsed.textContent = `已用 ${elapsedSeconds} 秒`;
}

function beginRefreshProgress() {
  clearRefreshProgressTimers();
  const startedAt = Date.now();
  updateRefreshProgress(startedAt);
  refreshProgressTimer = setInterval(() => updateRefreshProgress(startedAt), 1000);
  return startedAt;
}

function finishRefreshProgress(startedAt, state, label) {
  if (!startedAt) return;
  if (refreshProgressTimer) {
    clearInterval(refreshProgressTimer);
    refreshProgressTimer = null;
  }
  updateRefreshProgress(startedAt, state, label);
  refreshProgressHideTimer = setTimeout(() => {
    const progress = document.getElementById('refresh-progress');
    if (progress) {
      progress.hidden = true;
      progress.dataset.state = 'idle';
    }
  }, 2200);
}

function resetRefreshButton(button) {
  button.disabled = false;
  button.classList.remove('is-working');
  button.textContent = '立即获取最新 3 只';
}

function placeholderCard(rank) {
  return placeholderCardHTML(rank);
}

function cardFor(candidate, state) {
  return candidateCardHTML(candidate, state, { approvalsEnabled: approvalController?.enabled });
}

function compactPrice(value) {
  const price = Number(value);
  return Number.isFinite(price) ? `¥${price.toFixed(2)}` : '¥--.--';
}

function compactPct(value) {
  const pct = Number(value);
  if (!Number.isFinite(pct)) return '--.--%';
  return `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function compactAlertCard(candidate, triggeringCodes, showRepeat) {
  const rank = Math.min(3, Math.max(1, Number(candidate.rank) || 1));
  const level = levelMeta(candidate);
  const isTrigger = triggeringCodes.has(String(candidate.code));
  const changePct = Number(candidate.change_pct);
  const direction = changePct > 0 ? 'up' : (changePct < 0 ? 'down' : 'neutral');
  const repeat = showRepeat && candidate.repeat_active && candidate.repeat_label
    ? `<span class="repeat-badge">${esc(candidate.repeat_label)}</span>`
    : '';
  return `
    <article class="strong-alert-mini-card ${isTrigger ? 'is-trigger' : ''}">
      <span class="rank rank-${rank}-badge">${rank}</span>
      <div class="strong-alert-mini-identity">
        <strong class="strong-alert-mini-name">${esc(candidate.name || '待确认')}${repeat}</strong>
        <span class="strong-alert-mini-code">${esc(candidate.code || '—')}</span>
      </div>
      <span class="strong-alert-mini-pct" data-direction="${direction}">${compactPct(candidate.change_pct)}</span>
      <span class="strong-alert-mini-price">${compactPrice(candidate.price)}</span>
      <span class="level-tag level-${level.tone}">${esc(level.label.replace('级', ''))}</span>
    </article>`;
}

function alertMeta(triggerType) {
  if (triggerType === 'scheduled-09:45') {
    return { kicker: '上午固定提醒 · 09:45', title: '上午候选已到达固定观察时点' };
  }
  if (triggerType === 'scheduled-14:45') {
    return { kicker: '下午固定提醒 · 14:45', title: '下午候选已到达固定观察时点' };
  }
  return { kicker: '实时观察提醒', title: '盘中强异动' };
}

function closeAutomaticAlert() {
  const overlay = document.getElementById('strong-alerts');
  dismissedThroughAlertSequence = receivedAlertSequence;
  if (!overlay || !activeAutomaticAlert) return;
  overlay.classList.add('is-leaving');
  activeAutomaticAlert = null;
  automaticAlertQueue.length = 0;
  setTimeout(() => {
    if (activeAutomaticAlert) return;
    overlay.replaceChildren();
    overlay.hidden = true;
    overlay.classList.remove('is-leaving');
  }, 180);
}

function renderNextAutomaticAlert() {
  const overlay = document.getElementById('strong-alerts');
  if (!overlay || activeAutomaticAlert || !automaticAlertQueue.length) return;
  activeAutomaticAlert = automaticAlertQueue.shift();
  const { payload, state } = activeAutomaticAlert;
  const triggeringCodes = new Set((payload.triggering_codes || []).map(String));
  const snapshotCandidates = Array.isArray(payload.candidates) ? payload.candidates.slice(0, 3) : [];
  const stateCandidates = Array.isArray(state?.candidates) ? state.candidates.slice(0, 3) : [];
  const candidates = snapshotCandidates.length ? snapshotCandidates : stateCandidates;
  const showRepeat = payload.trigger_type === 'intraday';
  const triggerCandidate = candidates.find((candidate) => triggeringCodes.has(String(candidate.code)));
  const triggerName = triggerCandidate?.name || [...triggeringCodes][0] || '候选股票';
  const sectorName = triggerCandidate?.sector_name || '';
  const alertId = esc(payload.alert_id || Date.now());
  const meta = alertMeta(payload.trigger_type);
  const cards = candidates.length
    ? candidates.map((candidate) => compactAlertCard(candidate, triggeringCodes, showRepeat)).join('')
    : `<div class="strong-alert-syncing">${esc([...triggeringCodes].join('、') || '触发股票')} · 实时卡片同步中</div>`;
  overlay.hidden = false;
  overlay.classList.remove('is-leaving');
  overlay.innerHTML = `
  <section class="strong-alert-dialog" data-alert-id="${alertId}" role="alertdialog" aria-modal="false" aria-labelledby="automatic-alert-title" aria-describedby="automatic-alert-summary">
    <div class="strong-alert-header">
      <div>
        <span class="strong-alert-kicker">${esc(meta.kicker)}</span>
        <time datetime="${esc(payload.displayed_at || '')}">${fmtTime(payload.displayed_at)}</time>
      </div>
      <button type="button" class="strong-alert-dismiss" data-dismiss-alert aria-label="关闭自动提醒">关闭</button>
    </div>
    <h2 id="automatic-alert-title" class="strong-alert-title">${esc(meta.title)}</h2>
    <p id="automatic-alert-summary" class="strong-alert-summary">${esc(triggerName)}${sectorName ? ` · ${esc(sectorName)}` : ''} · 请及时查看</p>
    <div class="strong-alert-mini-grid">${cards}</div>
    <div class="strong-alert-footer">
      <span>只读观察提醒</span>
      <a class="strong-alert-open" href="/alerts">打开列表</a>
    </div>
  </section>`;
  overlay.querySelector('[data-dismiss-alert]')?.addEventListener('click', closeAutomaticAlert);
}

function showAutomaticAlert(payload, state, alertSequence) {
  if (!['intraday', 'scheduled-09:45', 'scheduled-14:45'].includes(payload.trigger_type)) return;
  if (alertSequence <= dismissedThroughAlertSequence) return;
  automaticAlertQueue.push({ payload, state, alertSequence });
  renderNextAutomaticAlert();
}

function formatRate(value) {
  return value == null ? '—' : `${(Number(value) * 100).toFixed(1)}%`;
}

function formatReturn(value) {
  if (value == null) return '—';
  const number = Number(value);
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

async function loadOutcomeSummary() {
  try {
    const payload = await apiJson('/api/v1/outcomes?range=month');
    const summary = payload.summary;
    const values = [
      ['个股胜率', formatRate(summary.win_rate)],
      ['日组合胜率', formatRate(payload.portfolio.win_rate)],
      ['平均收益', formatReturn(summary.average_return_pct)],
      ['已结算 / 总数', `${summary.settled_count} / ${summary.total_count}`],
    ];
    const target = document.getElementById('outcome-summary');
    if (target) {
      target.innerHTML = values.map(([label, value]) => `<article class="outcome-summary-card"><span>${esc(label)}</span><strong>${esc(value)}</strong></article>`).join('');
    }
    const backfill = document.getElementById('outcome-backfill');
    if (backfill) backfill.textContent = payload.backfill.message;
  } catch {
    const backfill = document.getElementById('outcome-backfill');
    if (backfill) backfill.textContent = '复盘暂时无法读取；当前候选观察不受影响。';
  }
}

function liveDateTimeLabel() {
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  const date = `${values.year}年${values.month}月${values.day}日`;
  return `${date}（${values.weekday}） ${values.hour}:${values.minute}:${values.second}`;
}

function updateLiveClock() {
  const clock = document.getElementById('live-clock');
  if (clock) clock.textContent = `当前时间 ${liveDateTimeLabel()}`;
}

function renderState(state) {
  latestDashboardState = state;
  const svc = document.getElementById('svc-state');
  if (svc) {
    const cls = ({ healthy: 'healthy', warming: 'warming', stale: 'stale', stopped: 'stopped' })[state.service_state] || 'warming';
    svc.textContent = stateLabels[state.service_state] || state.service_state || '启动中';
    svc.className = `status-item-value pill-${cls}`;
  }
  const market = document.getElementById('market-state');
  if (market) market.textContent = displayMarketPhase(state);
  const lastScan = document.getElementById('last-scan');
  const candidateTime = candidateTimestamp(state);
  if (lastScan) {
    lastScan.textContent = candidateTime ? fmtTime(candidateTime) : '尚无候选数据';
    lastScan.title = candidateTime ? '这三只候选对应的数据时间' : '尚未取得有效候选';
  }
  const scanCompleted = document.getElementById('scan-completed');
  if (scanCompleted) scanCompleted.textContent = state.last_scan?.completed_at
    ? `扫描完成 ${fmtTime(state.last_scan.completed_at)}` : '';
  const provenance = document.getElementById('candidate-provenance');
  if (provenance) {
    const retained = retainedCandidates(state);
    provenance.dataset.retained = String(retained);
    provenance.textContent = retained
      ? `保留快照 · ${fmtTime(candidateTime)}。当前未产生新的有效候选。`
      : (candidateTime ? '候选按原始排名展示，点击卡片查看原因。' : '候选尚未就绪，等待有效扫描。');
  }
  const workerAge = document.getElementById('worker-age');
  if (workerAge && state.worker_heartbeat_age_seconds != null) {
    workerAge.textContent = `最近检测 ${Math.round(state.worker_heartbeat_age_seconds)} 秒前`;
  } else if (workerAge) {
    workerAge.textContent = '';
  }
  updateLiveClock();
  const tasks = document.getElementById('tasks');
  if (tasks) {
    const list = state.tasks || [];
    tasks.innerHTML = list.length
      ? list.map((task) => `<span class="task">${esc(taskLabels[task.task_type] || task.task_type)}：${esc(taskStates[task.state] || task.state)}</span>`).join('')
      : '<span class="task muted">今日暂无自动任务</span>';
  }
  const cards = document.getElementById('cards');
  const candidates = Array.isArray(state.candidates) ? state.candidates : [];
  const candidateState = document.getElementById('candidate-state');
  if (candidateState) candidateState.textContent = candidates.length ? `${candidates.length}只观察` : '等待数据';
  const top3Title = document.getElementById('top3-title');
  if (top3Title) {
    const runLabel = state.service_state === 'healthy' ? '运行正常' : (stateLabels[state.service_state] || '同步中');
    top3Title.textContent = `${retainedCandidates(state) ? '上次' : '当前'} ${candidates.length} 只观察 · ${runLabel}`;
  }
  if (cards) {
    const candidatesByRank = new Map(candidates.map((candidate) => [Number(candidate.rank), candidate]));
    const oldCards = [...cards.querySelectorAll(':scope > article')];
    const focused = cards.contains(document.activeElement) ? document.activeElement : null;
    const used = new Set();
    [1,2,3].forEach((rank, index) => {
      const candidate = candidatesByRank.get(rank);
      const template = document.createElement('template');
      template.innerHTML = (candidate ? cardFor(candidate,state) : placeholderCard(rank)).trim();
      const fresh = template.content.firstElementChild;
      const previous = oldCards.find(card => !used.has(card) && card.dataset.detailCode === fresh.dataset.detailCode);
      const card = previous || fresh;
      if (previous) patchElement(previous, fresh);
      used.add(card);
      if (cards.children[index] !== card) cards.insertBefore(card, cards.children[index] || null);
      if (!previous) enter(card);
    });
    oldCards.forEach(card => { if (!used.has(card)) card.remove(); });
    if (focused?.isConnected && document.activeElement !== focused) focused.focus({preventScroll:true});
    let weak = cards.querySelector('.weak-note');
    if (state.overall_weak && candidates.length) {
      if (!weak) { weak = document.createElement('p'); weak.className = 'weak-note'; cards.append(weak); }
      weak.textContent = '本轮整体偏弱：正式候选不足三只，近/补位仅供参考';
    } else weak?.remove();

  }
  approvalController?.render(state);
}

let detailRequest = null;
let detailOriginCode = null;

async function showDetail(code, state) {
  const overlay = document.getElementById('drawer-overlay');
  const box = document.getElementById('detail');
  detailRequest?.abort();
  const controller = new AbortController();
  detailRequest = controller;
  detailOriginCode = code;
  box.innerHTML = '<p class="detail-loading" role="status">正在读取候选详情…</p>';
  box.setAttribute('aria-busy', 'true');
  openDrawer(overlay);
  try {
    if (state?.snapshot_id == null) throw new Error('候选快照暂不可用，请刷新后再试。');
    const detail = await apiJson(`/api/v1/candidates/${encodeURIComponent(code)}?snapshot_id=${state.snapshot_id}`, {signal:controller.signal});
    if (controller.signal.aborted || !overlay.open) return;
    const candidate = detail.candidate || {};
    const level = levelMeta(candidate);
    box.innerHTML = `
      <h2 class="display-name detail-stock-name">${esc(candidate.name)}</h2>
      <p class="display-code detail-stock-code">${esc(candidate.code)} · ${esc(candidate.sector_name || '—')}</p>
      <dl class="kv">
        <dt>数据时间</dt><dd>${esc(fmtTime(detail.source_ts))}</dd>
        <dt>候选级别</dt><dd>${esc(level.label)} · ${candidate.is_formal ? '正式观察' : '补位观察'}</dd>
        <dt>入选原因</dt><dd>${esc(candidate.explanation || '暂无进一步说明')}</dd>
      </dl>
      <details class="report-diagnostics"><summary>技术明细</summary>
        <p>快照 #${esc(detail.snapshot_id)}</p>
        <pre class="detail-factor-json">${esc(candidate.payload_json || '暂无因子明细')}</pre>
      </details>`;
    enhanceDetails(box);
    enter(box, 2);
  } catch (error) {
    if (controller.signal.aborted || !overlay.open) return;
    box.innerHTML = `<p class="error" role="alert">${error.status === 409 ? '候选列表已更新，请关闭后重新打开。' : '详情暂时无法读取，请重试。'}</p><button type="button" id="detail-retry" class="button-secondary">重新加载</button>`;
    document.getElementById('detail-retry').addEventListener('click', () => void showDetail(code, state));
  } finally {
    if (detailRequest === controller) box.setAttribute('aria-busy', 'false');
  }
}

function loadState() {
  if (stateRefreshPromise) {
    stateRefreshQueued = true;
    return stateRefreshPromise;
  }
  stateRefreshPromise = (async () => {
    let latest = null;
    do {
      stateRefreshQueued = false;
      try {
        latest = await apiJson('/api/v1/state');
        renderState(latest);
      } catch { /* WS/REST 双通道，断开时保留最后数据 */ }
    } while (stateRefreshQueued);
    return latest;
  })().finally(() => {
    const rerun = stateRefreshQueued;
    stateRefreshPromise = null;
    if (rerun) void loadState();
  });
  return stateRefreshPromise;
}

document.addEventListener('DOMContentLoaded', async () => {
  const approvalCards = document.getElementById('cards');
  approvalController = createApprovalController({
    cards: approvalCards, apiJson, userId: approvalCards?.dataset.approvalUser,
    status: document.getElementById('approval-status'),
    history: document.getElementById('approval-history'),
    pendingRoot: document.getElementById('approval-pending'),
  });
  updateLiveClock();
  setInterval(updateLiveClock, 1000);
  onEvent((event) => {
    if (event.event_type === 'server.resync_required') {
      handledAlertIds.clear();
      closeAutomaticAlert();
      void loadState();
      return;
    }
    if (event.event_type === 'state.snapshot' || event.event_type === 'state.changed' || event.event_type === 'candidates.updated') {
      if (event.event_type === 'state.snapshot') latestDashboardState = event.payload;
      void loadState();
    }
    if (event.event_type === 'alert.created') {
      const alertId = Number(event.payload?.alert_id || 0);
      if (alertId && handledAlertIds.has(alertId)) return;
      if (alertId) handledAlertIds.add(alertId);
      receivedAlertSequence += 1;
      const alertSequence = receivedAlertSequence;
      showAutomaticAlert(event.payload, latestDashboardState, alertSequence);
      void loadState();
      if (event.payload.trigger_type === 'intraday') {
        const code = event.payload.triggering_codes?.[0] || '候选股票';
        notify('盘中强异动', `${code} 触发强异动提醒，请及时查看`);
      } else {
        notify('StockWatcher 提醒', `触发：${event.payload.trigger_type}`);
      }
    }
    if (event.event_type === 'outcomes.updated') void loadOutcomeSummary();
  });
  connectEvents();
  await Promise.all([loadState(), loadOutcomeSummary()]);
  const refreshButton = document.getElementById('manual-refresh');
  refreshButton.addEventListener('click', async () => {
    const startedAt = beginRefreshProgress();
    refreshButton.disabled = true;
    refreshButton.classList.add('is-working');
    refreshButton.textContent = '正在获取最新 3 只';
    let pollTimer = null;
    let pollInFlight = false;
    let consecutivePollFailures = 0;
    let refreshFinished = false;
    const maxPollRetries = 4;
    const stopPolling = () => {
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
    };
    const schedulePoll = () => {
      if (refreshFinished || pollTimer) return;
      pollTimer = setTimeout(() => {
        pollTimer = null;
        void poll();
      }, 2000);
    };
    const finish = async (state, label, shouldLoadState = false) => {
      if (refreshFinished) return;
      refreshFinished = true;
      stopPolling();
      finishRefreshProgress(startedAt, state, label);
      resetRefreshButton(refreshButton);
      if (shouldLoadState) await loadState();
    };
    try {
      const result = await apiJson('/api/v1/commands/manual-refresh', {
        method: 'POST',
        headers: { 'Idempotency-Key': `manual-${Date.now()}` },
        body: '{}',
      });
      const commandId = result.command_id;
      const handleCommand = async (command) => {
        if (refreshFinished) return;
        if (command.status === 'succeeded') {
          await finish('done', '实时 Top3 已更新', true);
        } else if (command.status === 'failed') {
          const failure = refreshFailureLabels[command.error_code] || '刷新失败：未产生新候选';
          await finish('failed', failure, true);
        } else if (command.status === 'cancelled' || command.status === 'expired') {
          await finish('failed', '刷新已停止：未产生新候选', true);
        } else if (command.status === 'queued') {
          updateRefreshProgress(startedAt, 'working', '已排队，等待 Worker 领取');
          schedulePoll();
        } else if (command.status === 'running') {
          updateRefreshProgress(startedAt, 'working', 'Worker 正在扫描全市场');
          schedulePoll();
        } else if (Date.now() - startedAt > refreshCommandWaitMs) {
          await finish('timeout', 'Worker 仍未完成，请查看状态后再重试', true);
        } else {
          schedulePoll();
        }
      };
      const poll = async () => {
        if (refreshFinished || pollInFlight) return;
        pollInFlight = true;
        try {
          const command = await apiJson(`/api/v1/commands/${commandId}`);
          consecutivePollFailures = 0;
          await handleCommand(command);
        } catch {
          consecutivePollFailures += 1;
          if (consecutivePollFailures <= maxPollRetries) {
            updateRefreshProgress(
              startedAt,
              'working',
              `连接暂时中断，正在重试（${consecutivePollFailures}/${maxPollRetries}）`,
            );
            schedulePoll();
          } else {
            updateRefreshProgress(startedAt, 'working', '连接持续中断，正在确认刷新状态');
            try {
              const command = await apiJson(`/api/v1/commands/${commandId}`);
              consecutivePollFailures = 0;
              await handleCommand(command);
            } catch {
              await loadState();
              await finish('failed', '刷新连接中断，请稍后重试');
            }
          }
        } finally {
          pollInFlight = false;
        }
      };
      schedulePoll();
      await poll();
    } catch {
      await finishRefreshProgress(startedAt, 'failed', '刷新请求未开始');
      resetRefreshButton(refreshButton);
    }
  });
  const notifyButton = document.getElementById('notify-btn');
  function updateNotificationButton() {
    notifyButton.hidden = !('Notification' in window);
    if (notifyButton.hidden) return;
    notifyButton.disabled = Notification.permission !== 'default';
    notifyButton.textContent = Notification.permission === 'granted' ? '浏览器通知已开启' : Notification.permission === 'denied' ? '浏览器通知已关闭' : '开启浏览器通知';
    notifyButton.title = Notification.permission === 'denied' ? '可在浏览器的网站设置中更改通知权限' : '';
  }
  updateNotificationButton();
  notifyButton.addEventListener('click', async () => {
    const result = await requestNotificationPermission();
    updateNotificationButton();
    if (result === 'default') notifyButton.textContent = '暂不开启 · 点击重试';
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && activeAutomaticAlert && !document.getElementById('drawer-overlay').open) closeAutomaticAlert();
  });
  document.getElementById('cards').addEventListener('click', event => {
    if (event.target.closest('[data-approval-control]')) return;
    const card = event.target.closest('[data-detail-code]');
    if (card) void showDetail(card.dataset.detailCode, latestDashboardState);
  });
  const overlay = document.getElementById('drawer-overlay');
  document.getElementById('close-drawer-btn').addEventListener('click', () => closeDrawer(overlay));
  overlay.addEventListener('click', event => { if (event.target === overlay) closeDrawer(overlay); });
  overlay.addEventListener('dismissstart', () => detailRequest?.abort());
  overlay.addEventListener('close', () => {
    detailRequest?.abort();
    if (detailOriginCode) document.querySelector(`button[data-detail="${CSS.escape(detailOriginCode)}"]`)?.focus({preventScroll:true});
  });
  setInterval(loadState, 30000);
});
