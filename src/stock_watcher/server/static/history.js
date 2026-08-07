import { apiJson, esc, fmtTime } from './app.js';

let cursor = null;

function render(rows, append = false) {
  const wrap = document.getElementById('history');
  if (!rows.length) {
    if (!append) wrap.innerHTML = '<p class="muted">暂无历史观察。</p>';
    return;
  }
  const html = `
  <table>
    <thead><tr><th>快照</th><th>时间</th><th>健康</th><th>候选</th></tr></thead>
    <tbody>
      ${rows.map((row) => `
        <tr>
          <td>#${row.snapshot_id}</td>
          <td>${fmtTime(row.source_ts)}</td>
          <td>${esc(row.health)}${row.overall_weak ? ' <span class="weak-note">整体偏弱</span>' : ''}</td>
          <td>${esc((row.candidates || []).map((candidate) => `${candidate.rank}.${candidate.code}`).join('；'))}</td>
        </tr>`).join('')}
    </tbody>
  </table>`;
  if (append) wrap.insertAdjacentHTML('beforeend', html);
  else wrap.innerHTML = html;
}

async function load(append = false) {
  const params = new URLSearchParams({ limit: '50' });
  if (cursor != null) params.set('cursor', String(cursor));
  const from = document.getElementById('from').value;
  const to = document.getElementById('to').value;
  const code = document.getElementById('code').value.trim();
  if (from) params.set('from', from);
  if (to) params.set('to', to);
  if (code) params.set('code', code);
  const payload = await apiJson(`/api/v1/history?${params}`);
  render(payload.items, append);
  cursor = payload.next_cursor;
  document.getElementById('load-more').hidden = cursor == null;
}

document.addEventListener('DOMContentLoaded', () => {
  load();
  document.getElementById('filter-btn').addEventListener('click', () => {
    cursor = null;
    load();
  });
  document.getElementById('load-more').addEventListener('click', () => load(true));
});
