import { apiJson, connectEvents, esc, fmtTime, onEvent } from './app.js?v=6';

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
    <thead><tr><th>时间</th><th>类型</th><th>代码</th><th>决定</th></tr></thead>
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
  if (append) wrap.insertAdjacentHTML('beforeend', html);
  else wrap.innerHTML = html;
}

let cursor = null;

async function loadHistory(append = false) {
  const query = new URLSearchParams({ limit: '50' });
  if (cursor != null) query.set('cursor', String(cursor));
  const payload = await apiJson(`/api/v1/alerts?${query}`);
  renderHistory(payload.items, 'alerts-history', append);
  cursor = payload.next_cursor;
  document.getElementById('load-more').hidden = cursor == null;
}

document.addEventListener('DOMContentLoaded', async () => {
  const live = document.getElementById('alerts-live');
  onEvent((event) => {
    if (event.event_type === 'alert.created') {
      const payload = event.payload;
      live.innerHTML = `
        <article class="card">
          <h3>${esc(triggerLabels[payload.trigger_type] || payload.trigger_type)} @ ${fmtTime(payload.displayed_at)}</h3>
          <p>alert #${payload.alert_id} · snapshot #${payload.snapshot_id} · ${esc((payload.triggering_codes || []).join('、') || '—')}</p>
      </article>`;
    }
  });
  connectEvents();
  await loadHistory();
  document.getElementById('load-more').addEventListener('click', () => loadHistory(true));
});
