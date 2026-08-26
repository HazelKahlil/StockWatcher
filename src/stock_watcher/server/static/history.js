import { apiJson, esc, fmtTime } from './app.js?v=6';

let cursor = null;

function candidateCell(candidate) {
  const badge = candidate.repeat_active && candidate.repeat_label
    ? ` <span class="repeat-badge">${esc(candidate.repeat_label)}</span>`
    : '';
  const name = candidate.name || '待确认';
  const code = candidate.code || '—';
  return `${esc(name)} <span class="muted">${esc(code)}</span>${badge}`;
}

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
          <td>${(row.candidates || []).map(candidateCell).join('<br>')}</td>
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
  const repeatOnly = document.getElementById('repeat-only').checked;
  if (from) params.set('from', from);
  if (to) params.set('to', to);
  if (code) params.set('code', code);
  if (repeatOnly) params.set('repeat_active', 'true');
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
  document.getElementById('repeat-only').addEventListener('change', () => {
    cursor = null;
    load();
  });
  document.getElementById('load-more').addEventListener('click', () => load(true));
});
