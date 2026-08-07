import { api, apiJson, connectEvents, esc, fmtTime, onEvent, requestNotificationPermission, notify } from './app.js';

const stateLabels = { starting: '启动中', warming: '预热', healthy: '健康', stale: '陈旧', stopped: '停止' };
const marketLabels = { preopen: '盘前', morning: '上午盘', lunch: '午休', afternoon: '下午盘', closed: '休市' };

function cardFor(candidate, state) {
  const formal = candidate.is_formal ? '' : ' <span class="weak-note">补位</span>';
  const weak = state.overall_weak ? '<p class="weak-note">本轮整体偏弱：正式候选不足三只，近/补位仅供参考</p>' : '';
  return `
  <article class="card">
    <h3><span class="rank">${candidate.rank}</span>${esc(candidate.code)} · ${esc(candidate.name)}${formal}
      <span class="level-${esc(candidate.level)}">${esc(candidate.level)}</span></h3>
    <dl class="kv">
      <dt>价格</dt><dd>${Number(candidate.price).toFixed(2)}（${Number(candidate.change_pct).toFixed(2)}%）</dd>
      <dt>板块</dt><dd>${esc(candidate.sector_name || '—')}${candidate.sector_type ? `（${esc(candidate.sector_type)}）` : ''}</dd>
      <dt>综合分</dt><dd>${Number(candidate.total_score).toFixed(2)}</dd>
      <dt>资金</dt><dd>${esc(candidate.fund_label || '未确认')}</dd>
    </dl>
    <button type="button" data-detail="${esc(candidate.code)}">查看详情</button>
  </article>`;
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
  }
  const workerAge = document.getElementById('worker-age');
  if (workerAge && state.worker_heartbeat_age_seconds != null) {
    workerAge.textContent = `Worker心跳 ${Math.round(state.worker_heartbeat_age_seconds)}s 前`;
  }
  const tasks = document.getElementById('tasks');
  if (tasks) {
    const list = state.tasks || [];
    tasks.innerHTML = list.length
      ? list.map((task) => `<span class="task">${esc(task.task_type)}：${esc(task.state)}</span>`).join('')
      : '<span class="task muted">今日暂无自动任务</span>';
  }
  const cards = document.getElementById('cards');
  if (cards) {
    const candidates = state.candidates || [];
    cards.innerHTML = candidates.length
      ? candidates.map((candidate) => cardFor(candidate, state)).join('') + (state.overall_weak ? '<p class="weak-note">本轮整体偏弱：正式候选不足三只，近/补位仅供参考</p>' : '')
      : '<p class="muted">尚未形成合规三只；请等待健康快照或执行人工刷新。</p>';
  }
}

function showDetail(code, state) {
  const snapshotId = state.snapshot_id;
  if (snapshotId == null) { return; }
  apiJson(`/api/v1/candidates/${encodeURIComponent(code)}?snapshot_id=${snapshotId}`)
    .then((detail) => {
      const box = document.getElementById('detail');
      box.hidden = false;
      const candidate = detail.candidate || {};
      box.innerHTML = `
        <h2>详情 · ${esc(candidate.code)} ${esc(candidate.name)}</h2>
        <dl class="kv">
          <dt>快照</dt><dd>#${detail.snapshot_id} @ ${fmtTime(detail.source_ts)}</dd>
          <dt>级别</dt><dd>${esc(candidate.level)}${candidate.is_formal ? ' · 正式' : ' · 补位'}</dd>
          <dt>板块</dt><dd>${esc(candidate.sector_name || '—')}（${esc(candidate.sector_code || '—')}）</dd>
          <dt>解释</dt><dd>${esc(candidate.explanation || '—')}</dd>
          <dt>原始数据</dt><dd><pre class="table-wrap">${esc(candidate.payload_json || '')}</pre></dd>
        </dl>`;
    })
    .catch((error) => {
      if (error.status === 409) {
        const box = document.getElementById('detail');
        box.hidden = false;
        box.innerHTML = '<p class="weak-note">当前列表已更新：该详情绑定的是旧快照，请重新打开。</p>';
      }
    });
}

async function loadState() {
  try {
    const state = await apiJson('/api/v1/state');
    renderState(state);
  } catch { /* WS/REST 双通道，断开时保留最后数据 */ }
}

document.addEventListener('DOMContentLoaded', async () => {
  await loadState();
  connectEvents();
  onEvent((event) => {
    if (event.event_type === 'state.snapshot' || event.event_type === 'state.changed' || event.event_type === 'candidates.updated') {
      loadState();
    }
    if (event.event_type === 'alert.created') {
      notify('StockWatcher 提醒', `触发：${event.payload.trigger_type}`);
    }
  });
  const refreshButton = document.getElementById('manual-refresh');
  const commandState = document.getElementById('command-state');
  refreshButton.addEventListener('click', async () => {
    refreshButton.disabled = true;
    commandState.textContent = '正在排队…';
    try {
      const result = await apiJson('/api/v1/commands/manual-refresh', {
        method: 'POST',
        headers: { 'Idempotency-Key': `manual-${Date.now()}` },
        body: '{}',
      });
      commandState.textContent = `命令 ${result.command_id}：${result.status}${result.coalesced ? '（已合并到进行中的刷新）' : ''}，目标 60 秒`;
      const commandId = result.command_id;
      const started = Date.now();
      const timer = setInterval(async () => {
        try {
          const command = await apiJson(`/api/v1/commands/${commandId}`);
          commandState.textContent = `命令 ${commandId}：${command.status}`;
          if (command.status === 'succeeded' || command.status === 'failed') {
            clearInterval(timer);
            refreshButton.disabled = false;
            await loadState();
          } else if (Date.now() - started > 75000) {
            clearInterval(timer);
            refreshButton.disabled = false;
          }
        } catch {
          clearInterval(timer);
          refreshButton.disabled = false;
        }
      }, 2000);
    } catch (error) {
      commandState.textContent = error.message;
      refreshButton.disabled = false;
    }
  });
  const notifyButton = document.getElementById('notify-btn');
  notifyButton.hidden = !('Notification' in window);
  notifyButton.addEventListener('click', async () => {
    const result = await requestNotificationPermission();
    notifyButton.textContent = result === 'granted' ? '浏览器通知已开启' : '通知被拒绝';
  });
  document.getElementById('cards').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-detail]');
    if (!button) return;
    apiJson('/api/v1/state').then((state) => showDetail(button.dataset.detail, state));
  });
  setInterval(loadState, 30000);
});
