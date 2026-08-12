import { api, apiJson, connectEvents, esc, fmtTime, onEvent, requestNotificationPermission, notify } from './app.js?v=5';

const stateLabels = { starting: '启动中', warming: '预热', healthy: '健康', stale: '陈旧', stopped: '停止' };
const marketLabels = { preopen: '盘前', morning: '上午盘', lunch: '午休', afternoon: '下午盘', closed: '休市' };
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
let refreshProgressTimer = null;
let refreshProgressHideTimer = null;
const handledAlertIds = new Set();
let connectionWatermark = null;
const automaticAlertQueue = [];
let activeAutomaticAlert = null;
let alertRestoreTarget = null;

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

function levelMeta(candidate) {
  const raw = String(candidate.level || '');
  if (raw.includes('强')) return { label: '强级', tone: 'strong' };
  if (raw.includes('中')) return { label: '中级', tone: 'medium' };
  return { label: candidate.is_formal ? '近级' : '近级补位', tone: 'near' };
}

function placeholderCard(rank) {
  const rankStr = String(rank).padStart(2, '0');
  return `
  <article class="card placeholder-card" aria-label="等待抓取第 ${rank} 只候选">
    <div class="card-top-tag">
      <span class="rank rank-${rank}-badge">${rankStr}</span>
      <span class="level-tag level-placeholder">待抓取</span>
    <span class="placeholder-state">
      <span class="placeholder-state-label">等待数据</span>
      <span class="placeholder-dots" aria-hidden="true"><span class="placeholder-dot">·</span><span class="placeholder-dot">·</span><span class="placeholder-dot">·</span></span>
    </span>
    </div>

    <div class="card-stock-hero">
      <div class="stock-display">
        <div class="stock-name-row">
          <h2 class="display-name placeholder-text">····</h2>
          <span class="sector-tag placeholder-text">板块待抓取</span>
        </div>
        <div class="display-code-row">
          <span class="display-code placeholder-text">······</span>
        </div>
      </div>
    </div>

    <div class="price-fund-block">
      <div class="price-main">
        <span class="ashare-price placeholder-text">--.--</span>
        <span class="ashare-pct placeholder-text">--.--%</span>
      </div>
    </div>

    <div class="card-footer">
      <span class="placeholder-card-status">正在抓取</span>
    </div>
  </article>`;
}

function cardFor(candidate, state) {
  const isRank1 = candidate.rank === 1;
  const level = levelMeta(candidate);
  const formal = candidate.is_formal ? '<span class="formal-pill">正式候选</span>' : '<span class="weak-note">补位</span>';
  const price = Number(candidate.price).toFixed(2);
  const changePct = Number(candidate.change_pct);
  const pctStr = (changePct > 0 ? '+' : '') + changePct.toFixed(2) + '%';
  const direction = changePct > 0 ? 'up' : (changePct < 0 ? 'down' : 'neutral');
  const rankStr = String(candidate.rank).padStart(2, '0');

  return `
  <article class="card ${isRank1 ? 'rank-1-card' : ''}">
    <div class="card-top-tag">
      <span class="rank rank-${candidate.rank}-badge">${rankStr}</span>
      <span class="level-tag level-${level.tone}">${esc(level.label)}</span>
      ${formal}
    </div>

    <div class="card-stock-hero">
      <div class="stock-display">
        <div class="stock-name-row">
          <h2 class="display-name">${esc(candidate.name)}</h2>
          ${candidate.sector_name ? `<span class="sector-tag">${esc(candidate.sector_name)}</span>` : ''}
        </div>
        <div class="display-code-row">
          <span class="display-code">${esc(candidate.code)}</span>
        </div>
      </div>
    </div>

    <div class="price-fund-block">
      <div class="price-main">
        <span class="ashare-price">¥${price}</span>
        <span class="ashare-pct" data-direction="${direction}">${pctStr}</span>
      </div>
    </div>

    <div class="card-footer">
      <div class="metric-row">
        <span>得分 <strong>${Number(candidate.total_score).toFixed(1)}</strong></span>
        <span>阶段 <strong>${esc(level.label)}</strong></span>
      </div>
      <button type="button" class="btn-detail" data-detail="${esc(candidate.code)}">因子审计</button>
    </div>
  </article>`;
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

function compactAlertCard(candidate, triggeringCodes) {
  const rank = Math.min(3, Math.max(1, Number(candidate.rank) || 1));
  const level = levelMeta(candidate);
  const isTrigger = triggeringCodes.has(String(candidate.code));
  const rankStr = String(rank).padStart(2, '0');
  const changePct = Number(candidate.change_pct);
  const direction = changePct > 0 ? 'up' : (changePct < 0 ? 'down' : 'neutral');
  return `
    <article class="strong-alert-mini-card ${isTrigger ? 'is-trigger' : ''}">
      <div class="strong-alert-mini-top">
        <span class="rank rank-${rank}-badge">${rankStr}</span>
        <span class="level-tag level-${level.tone}">${esc(level.label)}</span>
      </div>
      <strong class="strong-alert-mini-name">${esc(candidate.name || '待确认')}</strong>
      <span class="strong-alert-mini-code">${esc(candidate.code || '—')}</span>
      <div class="strong-alert-mini-market">
        <span class="strong-alert-mini-price">${compactPrice(candidate.price)}</span>
        <span class="strong-alert-mini-pct" data-direction="${direction}">${compactPct(candidate.change_pct)}</span>
      </div>
    </article>`;
}

function alertMeta(triggerType) {
  if (triggerType === 'scheduled-09:45') {
    return { kicker: '上午固定提醒 · 09:45', title: '上午候选已到达固定观察时点' };
  }
  if (triggerType === 'scheduled-14:45') {
    return { kicker: '下午固定提醒 · 14:45', title: '下午候选已到达固定观察时点' };
  }
  return { kicker: '盘中强异动', title: '发现强异动信号' };
}

function closeAutomaticAlert() {
  const overlay = document.getElementById('strong-alerts');
  if (!overlay || !activeAutomaticAlert) return;
  overlay.classList.add('is-leaving');
  const closing = activeAutomaticAlert;
  activeAutomaticAlert = null;
  setTimeout(() => {
    if (activeAutomaticAlert) return;
    overlay.replaceChildren();
    overlay.hidden = true;
    overlay.classList.remove('is-leaving');
    document.body.classList.remove('alert-open');
    if (automaticAlertQueue.length) {
      renderNextAutomaticAlert();
      return;
    }
    if (alertRestoreTarget?.isConnected) alertRestoreTarget.focus();
    alertRestoreTarget = null;
  }, closing ? 180 : 0);
}

function renderNextAutomaticAlert() {
  const overlay = document.getElementById('strong-alerts');
  if (!overlay || activeAutomaticAlert || !automaticAlertQueue.length) return;
  activeAutomaticAlert = automaticAlertQueue.shift();
  const { payload, state } = activeAutomaticAlert;
  const triggeringCodes = new Set((payload.triggering_codes || []).map(String));
  const candidates = Array.isArray(state?.candidates) ? state.candidates.slice(0, 3) : [];
  const triggerCandidate = candidates.find((candidate) => triggeringCodes.has(String(candidate.code)));
  const triggerName = triggerCandidate?.name || [...triggeringCodes][0] || '候选股票';
  const sectorName = triggerCandidate?.sector_name || '';
  const alertId = esc(payload.alert_id || Date.now());
  const meta = alertMeta(payload.trigger_type);
  const cards = candidates.length
    ? candidates.map((candidate) => compactAlertCard(candidate, triggeringCodes)).join('')
    : `<div class="strong-alert-syncing">${esc([...triggeringCodes].join('、') || '触发股票')} · 实时卡片同步中</div>`;
  if (!alertRestoreTarget) alertRestoreTarget = document.activeElement;
  overlay.hidden = false;
  overlay.classList.remove('is-leaving');
  document.body.classList.add('alert-open');
  overlay.innerHTML = `
  <section class="strong-alert-dialog" data-alert-id="${alertId}" role="alertdialog" aria-modal="true" aria-labelledby="automatic-alert-title" aria-describedby="automatic-alert-summary">
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
      <a href="/alerts">查看提醒中心</a>
      <span>此提醒需要手动关闭</span>
    </div>
  </section>`;
  overlay.querySelector('[data-dismiss-alert]')?.addEventListener('click', closeAutomaticAlert);
  overlay.querySelector('[data-dismiss-alert]')?.focus();
}

function showAutomaticAlert(payload, state) {
  if (!['intraday', 'scheduled-09:45', 'scheduled-14:45'].includes(payload.trigger_type)) return;
  automaticAlertQueue.push({ payload, state });
  renderNextAutomaticAlert();
}

function keepAlertFocus(event) {
  if (event.key !== 'Tab' || !activeAutomaticAlert) return;
  const dialog = document.querySelector('.strong-alert-dialog');
  if (!dialog) return;
  const focusable = [...dialog.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
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
      ['个人胜率', formatRate(summary.win_rate)],
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
  return {
    titleDate: `${date}（${values.weekday}）`,
    dateTime: `${date} ${values.hour}:${values.minute}:${values.second}`,
  };
}

function updateLiveClock() {
  const { titleDate, dateTime } = liveDateTimeLabel();
  const title = document.getElementById('top3-title');
  if (title) title.textContent = `${titleDate} 实时Top3`;
  const clock = document.getElementById('live-clock');
  if (clock) clock.textContent = `当前时间 ${dateTime}`;
}

function renderState(state) {
  const svc = document.getElementById('svc-state');
  if (svc) {
    const cls = ({ healthy: 'healthy', warming: 'warming', stale: 'stale', stopped: 'stopped' })[state.service_state] || 'warming';
    svc.textContent = stateLabels[state.service_state] || state.service_state || '启动中';
    svc.className = `pill pill-${cls}`;
  }
  const market = document.getElementById('market-state');
  if (market) market.textContent = `市场：${marketLabels[state.market_state] || state.market_state || '—'}`;
  const lastScan = document.getElementById('last-scan');
  if (lastScan && state.last_scan) {
    lastScan.textContent = `最后扫描 ${fmtTime(state.last_scan.completed_at)} · 覆盖 ${(state.last_scan.coverage_ratio * 100).toFixed(1)}% · 耗时 ${Number(state.last_scan.elapsed_seconds).toFixed(1)}s`;
  } else if (lastScan && state.source_ts) {
    lastScan.textContent = `最后扫描 ${fmtTime(state.source_ts)} · 使用上一份实时结果`;
  } else if (lastScan) {
    lastScan.textContent = '尚未完成实时扫描';
  }
  const workerAge = document.getElementById('worker-age');
  if (workerAge && state.worker_heartbeat_age_seconds != null) {
    workerAge.textContent = `Worker心跳 ${Math.round(state.worker_heartbeat_age_seconds)}s 前`;
  }
  updateLiveClock();
  const tasks = document.getElementById('tasks');
  if (tasks) {
    const list = state.tasks || [];
    tasks.innerHTML = list.length
      ? list.map((task) => `<span class="task">${esc(task.task_type)}：${esc(task.state)}</span>`).join('')
      : '<span class="task muted">今日暂无自动任务</span>';
  }
  const cards = document.getElementById('cards');
  if (cards) {
    const candidates = Array.isArray(state.candidates) ? state.candidates : [];
    const candidatesByRank = new Map(candidates.map((candidate) => [Number(candidate.rank), candidate]));
    const cardsMarkup = [1, 2, 3].map((rank) => {
      const candidate = candidatesByRank.get(rank);
      return candidate ? cardFor(candidate, state) : placeholderCard(rank);
    }).join('');
    cards.innerHTML = cardsMarkup + (state.overall_weak && candidates.length ? '<p class="weak-note">本轮整体偏弱：正式候选不足三只，近/补位仅供参考</p>' : '');
  }
}

function showDetail(code, state) {
  const snapshotId = state.snapshot_id;

  function renderDetailPayload(candidate, snapId, srcTs) {
    const detailLevel = levelMeta(candidate);
    const overlay = document.getElementById('drawer-overlay');
    const box = document.getElementById('detail');
    if (overlay) overlay.hidden = false;
    if (box) {
      box.hidden = false;
      box.innerHTML = `
        <h2 class="display-name detail-stock-name">${esc(candidate.name)}</h2>
        <div class="display-code detail-stock-code">${esc(candidate.code)} · ${esc(candidate.sector_name || '—')}</div>
        <dl class="kv">
          <dt>快照</dt><dd>#${snapId || 'DEMO'} @ ${srcTs ? fmtTime(srcTs) : '样板时间'}</dd>
          <dt>级别</dt><dd>${esc(detailLevel.label)}${candidate.is_formal ? ' · 正式' : ' · 补位'}</dd>
          <dt>板块</dt><dd>${esc(candidate.sector_name || '—')}</dd>
          <dt>核心得因</dt><dd>${esc(candidate.explanation || '—')}</dd>
          <dt>因子 JSON</dt><dd><pre class="table-wrap detail-factor-json">${esc(candidate.payload_json || '')}</pre></dd>
        </dl>`;
    }
  }

  if (snapshotId == null) return;

  apiJson(`/api/v1/candidates/${encodeURIComponent(code)}?snapshot_id=${snapshotId}`)
    .then((detail) => {
      renderDetailPayload(detail.candidate || {}, detail.snapshot_id, detail.source_ts);
    })
    .catch((error) => {
      const overlay = document.getElementById('drawer-overlay');
      const box = document.getElementById('detail');
      if (overlay) overlay.hidden = false;
      if (box) {
        box.hidden = false;
        box.innerHTML = error.status === 409
          ? '<p class="weak-note">当前列表已更新：该详情绑定的是旧快照，请重新打开。</p>'
          : `<p class="error">加载详情失败：${esc(error.message)}</p>`;
      }
    });
}

async function loadState() {
  try {
    const state = await apiJson('/api/v1/state');
    renderState(state);
    return state;
  } catch { /* WS/REST 双通道，断开时保留最后数据 */ }
  return null;
}

document.addEventListener('DOMContentLoaded', async () => {
  updateLiveClock();
  setInterval(updateLiveClock, 1000);
  await loadState();
  await loadOutcomeSummary();
  onEvent(async (event) => {
    if (event.event_type === 'server.hello') {
      connectionWatermark = Number(event.payload?.latest_event_id || 0);
      return;
    }
    if (event.event_type === 'state.snapshot' || event.event_type === 'state.changed' || event.event_type === 'candidates.updated') {
      loadState();
    }
    if (event.event_type === 'alert.created') {
      const eventId = Number(event.event_id || 0);
      if (connectionWatermark != null && eventId <= connectionWatermark) return;
      const alertId = Number(event.payload?.alert_id || 0);
      if (alertId && handledAlertIds.has(alertId)) return;
      if (alertId) handledAlertIds.add(alertId);
      const state = await loadState();
      showAutomaticAlert(event.payload, state);
      if (event.payload.trigger_type === 'intraday') {
        const code = event.payload.triggering_codes?.[0] || '候选股票';
        notify('盘中强异动', `${code} 触发强异动提醒，请及时查看`);
      } else {
        notify('StockWatcher 提醒', `触发：${event.payload.trigger_type}`);
      }
    }
    if (event.event_type === 'outcomes.updated') loadOutcomeSummary();
  });
  connectEvents();
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
  notifyButton.hidden = !('Notification' in window);
  notifyButton.addEventListener('click', async () => {
    const result = await requestNotificationPermission();
    notifyButton.textContent = result === 'granted' ? '浏览器通知已开启' : '通知被拒绝';
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && activeAutomaticAlert) closeAutomaticAlert();
    keepAlertFocus(event);
  });
  document.getElementById('cards').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-detail]');
    if (!button) return;
    apiJson('/api/v1/state').then((state) => showDetail(button.dataset.detail, state));
  });
  const closeBtn = document.getElementById('close-drawer-btn');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      const overlay = document.getElementById('drawer-overlay');
      if (overlay) overlay.hidden = true;
    });
  }
  setInterval(loadState, 30000);
});
