import { apiJson, esc, fmtTime } from './app.js?v=8';

let cursor = null;
let controller = null;
let visibleCount = 0;
let lastAppend = false;
let loadedFilters = new URLSearchParams();
const healthLabels = { HEALTHY:'正常', WARMING:'预热', STALE:'已过时', STOPPED:'已停止', RED:'已停止' };

function candidateCell(candidate) {
  const badge = candidate.repeat_active && candidate.repeat_label
    ? ` <span class="repeat-badge">${esc(candidate.repeat_label)}</span>` : '';
  return `${esc(candidate.name || '待确认')} <span class="muted">${esc(candidate.code || '—')}</span>${badge}`;
}

function render(rows, append) {
  const wrap = document.getElementById('history');
  if (!rows.length) {
    if (!append) wrap.innerHTML = '<div class="empty-state"><strong>这个范围内没有观察记录</strong>可调整筛选条件，或点击重置查看全部。</div>';
    return;
  }
  const body = rows.map(row => `<tr><td>#${esc(row.snapshot_id)}</td><td>${fmtTime(row.source_ts)}</td><td>${esc(healthLabels[row.health] || row.health)}${row.overall_weak ? '<small>整体偏弱</small>' : ''}</td><td>${(row.candidates || []).map(candidateCell).join('<br>')}</td></tr>`).join('');
  if (append && wrap.querySelector('tbody')) wrap.querySelector('tbody').insertAdjacentHTML('beforeend', body);
  else wrap.innerHTML = `<table><thead><tr><th>快照</th><th>数据时间</th><th>数据状态</th><th>候选</th></tr></thead><tbody>${body}</tbody></table>`;
}

async function load(append = false) {
  controller?.abort();
  const request = new AbortController();
  controller = request;
  lastAppend = append;
  const status = document.getElementById('history-status');
  const from = document.getElementById('from').value;
  const to = document.getElementById('to').value;
  document.getElementById('to').setCustomValidity(from && to && from > to ? '结束日期不能早于开始日期' : '');
  if (!append && !document.getElementById('history-filters').reportValidity()) {
    document.getElementById('load-more').disabled = false;
    document.getElementById('history').setAttribute('aria-busy', 'false');
    status.textContent = '请检查日期范围。';
    return;
  }
  const params = append ? new URLSearchParams(loadedFilters) : new URLSearchParams({limit:'50'});
  if (append && cursor != null) params.set('cursor', String(cursor));
  if (!append && from) params.set('from', from);
  if (!append && to) params.set('to', to);
  const code = document.getElementById('code').value.trim().toUpperCase();
  if (!append && code) params.set('code', code);
  if (!append && document.getElementById('repeat-only').checked) params.set('repeat_active', 'true');
  status.dataset.error = 'false';
  status.textContent = append ? '正在加载更多…' : '正在筛选观察记录…';
  document.getElementById('history-retry').hidden = true;
  document.getElementById('load-more').disabled = true;
  document.getElementById('history').setAttribute('aria-busy', 'true');
  try {
    const payload = await apiJson(`/api/v1/history?${params}`, {signal:request.signal});
    if (request.signal.aborted) return;
    render(payload.items, append);
    if (!append) loadedFilters = new URLSearchParams(params);
    visibleCount = (append ? visibleCount : 0) + payload.items.length;
    cursor = payload.next_cursor;
    document.getElementById('load-more').hidden = cursor == null;
    status.textContent = `已显示 ${visibleCount} 轮观察${cursor == null ? ' · 已到最后' : ''}`;
  } catch {
    if (request.signal.aborted) return;
    status.dataset.error = 'true';
    status.textContent = `加载失败${visibleCount ? '，保留上次显示的记录' : ''}。请重试。`;
    document.getElementById('history-retry').hidden = false;
  } finally {
    if (controller === request) {
      document.getElementById('load-more').disabled = false;
      document.getElementById('history').setAttribute('aria-busy', 'false');
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('history-filters').addEventListener('submit', event => { event.preventDefault(); void load(); });
  document.getElementById('history-filters').addEventListener('reset', () => {
    document.getElementById('to').setCustomValidity('');
    setTimeout(() => void load(), 0);
  });
  document.getElementById('repeat-only').addEventListener('change', () => void load());
  document.getElementById('to').addEventListener('input', event => event.target.setCustomValidity(''));
  document.getElementById('from').addEventListener('input', () => document.getElementById('to').setCustomValidity(''));
  document.getElementById('history-retry').addEventListener('click', () => void load(lastAppend));
  document.getElementById('load-more').addEventListener('click', () => void load(true));
  void load();
});
