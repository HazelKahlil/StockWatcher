import { apiJson, connectEvents, esc, fmtTime, onEvent } from './app.js?v=7';

const triggerLabels = {
  'scheduled-09:45': '09:45 观察',
  'scheduled-14:45': '14:45 观察',
  intraday: '盘中强异动',
};

function renderHistory(rows, container, append = false) {
  const wrap = document.getElementById(container);
  if (!rows.length) {
    if (!append) wrap.innerHTML = '<p class="muted">暂无提醒记录。</p>';
    return;
  }
  const html = `
  <table>
    <thead><tr><th>时间</th><th>类型</th><th>代码</th><th>展示状态</th></tr></thead>
    <tbody>
      ${rows.map((row) => `
        <tr>
          <td>${fmtTime(row.displayed_at)}</td>
          <td>${esc(triggerLabels[row.trigger_type] || row.trigger_type)}</td>
          <td>${esc((row.triggering_codes || []).join('、') || '—')}</td>
          <td>${esc(row.decision || '—')}</td>
        </tr>`).join('')}
    </tbody>
  </table>`;
  if (append && wrap.querySelector('tbody')) {
    const template = document.createElement('template');
    template.innerHTML = html;
    wrap.querySelector('tbody').append(...template.content.querySelector('tbody').children);
  } else wrap.innerHTML = html;
}

let cursor = null;

let loading = false;
let retryAppend = false;
let visibleCount = 0;
async function loadHistory(append = false) {
  if (loading) return;
  loading = true;
  retryAppend = append;
  const status = document.getElementById('alerts-status');
  const more = document.getElementById('load-more');
  more.disabled = true;
  document.getElementById('alerts-retry').hidden = true;
  status.dataset.error = 'false';
  status.textContent = '正在读取提醒记录…';
  const query = new URLSearchParams({ limit: '50' });
  if (append && cursor != null) query.set('cursor', String(cursor));
  try {
    const payload = await apiJson(`/api/v1/alerts?${query}`);
    renderHistory(payload.items, 'alerts-history', append);
    visibleCount = (append ? visibleCount : 0) + payload.items.length;
    cursor = payload.next_cursor;
    more.hidden = cursor == null;
    status.textContent = `已显示 ${visibleCount} 条提醒${cursor == null ? ' · 已到最后' : ''}`;
  } catch {
    status.dataset.error = 'true';
    status.textContent = '提醒记录暂时无法读取，请重试。';
    document.getElementById('alerts-retry').hidden = false;
  } finally {
    more.disabled = false;
    loading = false;
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  const live = document.getElementById('alerts-live');
  onEvent((event) => {
    if (event.event_type === 'alert.created') {
      const payload = event.payload;
      live.innerHTML = `
        <article class="card">
          <h3>${esc(triggerLabels[payload.trigger_type] || payload.trigger_type)} @ ${fmtTime(payload.displayed_at)}</h3>
          <p>${esc((payload.triggering_codes || []).join('、') || '候选记录已更新')}</p><a href="/">查看当前观察</a>
      </article>`;
    }
  });
  connectEvents();
  document.getElementById('alerts-retry').addEventListener('click', () => void loadHistory(retryAppend));
  await loadHistory();
  document.getElementById('load-more').addEventListener('click', () => loadHistory(true));
});
