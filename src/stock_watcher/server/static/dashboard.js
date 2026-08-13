import { api, apiJson, connectEvents, esc, fmtTime, onEvent, requestNotificationPermission, notify } from './app.js?v=6';

const stateLabels = { starting: '启动中', warming: '预热', healthy: '正常', stale: '陈旧', stopped: '停止' };
const marketLabels = { preopen: '盘前', morning: '上午盘', lunch: '午休', afternoon: '下午盘', closed: '休市', unknown: '待确认' };
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
const automaticAlertQueue = [];
let activeAutomaticAlert = null;

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
  return `
  <article class="card placeholder-card" aria-label="等待抓取第 ${rank} 只候选">
    <span class="rank rank-${rank}-badge">${rank}</span>
    <div class="candidate-identity">
      <h3 class="display-name placeholder-text">等待候选</h3>
      <span class="display-code placeholder-text">------</span>
    </div>
    <div class="candidate-quote">
      <span class="ashare-pct placeholder-text">--.--%</span>
      <span class="ashare-price placeholder-text">¥--.--</span>
    </div>
    <span class="level-tag level-placeholder">待</span>
    <div class="candidate-sector">
      <span class="candidate-meta-label">最强板块</span>
      <strong class="placeholder-text">板块待抓取</strong>
      <small class="placeholder-card-status">正在抓取<span class="placeholder-dots" aria-hidden="true"><span class="placeholder-dot">·</span><span class="placeholder-dot">·</span><span class="placeholder-dot">·</span></span></small>
    </div>
    <span class="card-arrow" aria-hidden="true">›</span>
  </article>`;
}

function cardFor(candidate, state) {
  const level = levelMeta(candidate);
  const price = Number(candidate.price).toFixed(2);
  const changePct = Number(candidate.change_pct);
  const pctStr = (changePct > 0 ? '+' : '') + changePct.toFixed(2) + '%';
  const direction = changePct > 0 ? 'up' : (changePct < 0 ? 'down' : 'neutral');
  const fundLabel = state?.fund_module && state.fund_module !== 'unavailable'
    ? '资金增强可用'
    : '资金未确认';
  const observationLabel = candidate.is_formal ? fundLabel : `补位观察 · ${fundLabel}`;

  return `
  <article class="card ${candidate.rank === 1 ? 'rank-1-card' : ''}">
    <span class="rank rank-${candidate.rank}-badge">${candidate.rank}</span>
    <div class="candidate-identity">
      <h3 class="display-name">${esc(candidate.name)}</h3>
      <span class="display-code">${esc(candidate.code)}</span>
    </div>
    <div class="candidate-quote">
      <span class="ashare-pct" data-direction="${direction}">${pctStr}</span>
      <span class="ashare-price">¥${price}</span>
    </div>
    <span class="level-tag level-${level.tone}">${esc(level.label.replace('级', ''))}</span>
    <div class="candidate-sector">
      <span class="candidate-meta-label">最强板块</span>
      <strong>${esc(candidate.sector_name || '板块待确认')}</strong>
      <small>${esc(observationLabel)}</small>
    </div>
    <button type="button" class="card-open-detail" data-detail="${esc(candidate.code)}" aria-label="查看 ${esc(candidate.name)} 因子审计"><span aria-hidden="true">›</span></button>
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
  const changePct = Number(candidate.change_pct);
  const direction = changePct > 0 ? 'up' : (changePct < 0 ? 'down' : 'neutral');
  return `
    <article class="strong-alert-mini-card ${isTrigger ? 'is-trigger' : ''}">
      <span class="rank rank-${rank}-badge">${rank}</span>
      <div class="strong-alert-mini-identity">
        <strong class="strong-alert-mini-name">${esc(candidate.name || '待确认')}</strong>
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
  const candidates = Array.isArray(state?.candidates) ? state.candidates.slice(0, 3) : [];
  const triggerCandidate = candidates.find((candidate) => triggeringCodes.has(String(candidate.code)));
  const triggerName = triggerCandidate?.name || [...triggeringCodes][0] || '候选股票';
  const sectorName = triggerCandidate?.sector_name || '';
  const alertId = esc(payload.alert_id || Date.now());
  const meta = alertMeta(payload.trigger_type);
  const cards = candidates.length
    ? candidates.map((candidate) => compactAlertCard(candidate, triggeringCodes)).join('')
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

function showAutomaticAlert(payload, state) {
  if (!['intraday', 'scheduled-09:45', 'scheduled-14:45'].includes(payload.trigger_type)) return;
  automaticAlertQueue.push({ payload, state });
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
  return `${date}（${values.weekday}） ${values.hour}:${values.minute}:${values.second}`;
}

function updateLiveClock() {
  const clock = document.getElementById('live-clock');
  if (clock) clock.textContent = `当前时间 ${liveDateTimeLabel()}`;
}

function renderState(state) {
  const svc = document.getElementById('svc-state');
  if (svc) {
    const cls = ({ healthy: 'healthy', warming: 'warming', stale: 'stale', stopped: 'stopped' })[state.service_state] || 'warming';
    svc.textContent = stateLabels[state.service_state] || state.service_state || '启动中';
    svc.className = `status-item-value pill-${cls}`;
  }
  const market = document.getElementById('market-state');
  if (market) market.textContent = marketLabels[state.market_state] || state.market_state || '—';
  const lastScan = document.getElementById('last-scan');
  if (lastScan && state.last_scan) {
    lastScan.textContent = fmtTime(state.last_scan.completed_at);
    lastScan.title = `覆盖 ${(state.last_scan.coverage_ratio * 100).toFixed(1)}% · 耗时 ${Number(state.last_scan.elapsed_seconds).toFixed(1)}s`;
  } else if (lastScan && state.source_ts) {
    lastScan.textContent = fmtTime(state.source_ts);
    lastScan.title = '使用上一份实时结果';
  } else if (lastScan) {
    lastScan.textContent = '尚未完成实时扫描';
    lastScan.removeAttribute('title');
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
      ? list.map((task) => `<span class="task">${esc(task.task_type)}：${esc(task.state)}</span>`).join('')
      : '<span class="task muted">今日暂无自动任务</span>';
  }
  const cards = document.getElementById('cards');
  const candidates = Array.isArray(state.candidates) ? state.candidates : [];
  const candidateState = document.getElementById('candidate-state');
  if (candidateState) candidateState.textContent = candidates.length ? `${candidates.length}只观察` : '等待数据';
  const top3Title = document.getElementById('top3-title');
  if (top3Title) {
    const runLabel = state.service_state === 'healthy' ? '运行正常' : (stateLabels[state.service_state] || '同步中');
    top3Title.textContent = `当前${candidates.length}只观察｜${runLabel}`;
  }
  if (cards) {
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
  onEvent(async (event) => {
    if (event.event_type === 'state.snapshot' || event.event_type === 'state.changed' || event.event_type === 'candidates.updated') {
      loadState();
    }
    if (event.event_type === 'alert.created') {
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
  notifyButton.hidden = !('Notification' in window);
  notifyButton.addEventListener('click', async () => {
    const result = await requestNotificationPermission();
    notifyButton.textContent = result === 'granted' ? '浏览器通知已开启' : '通知被拒绝';
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && activeAutomaticAlert) closeAutomaticAlert();
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
