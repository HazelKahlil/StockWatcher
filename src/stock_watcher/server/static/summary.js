import { apiJson, esc, fmtTime } from './app.js';

function render(items) {
  const wrap = document.getElementById('summaries');
  if (!items.length) {
    wrap.innerHTML = '<p class="muted">暂无盘后总结；15:30 后自动生成（本地优先）。</p>';
    return;
  }
  wrap.innerHTML = items.map((item) => `
    <article class="card">
      <h3>${esc(item.trade_date)} · ${item.catch_up ? '补生成' : '正常'} · ${esc(item.version || '—')}</h3>
      <p class="muted">生成于 ${fmtTime(item.generated_at)} · 提醒 ${item.alert_count} 条</p>
      <p><a href="/api/v1/summaries/${esc(item.trade_date)}">查看 JSON</a>
         · <a href="/api/v1/summaries/${esc(item.trade_date)}/pdf" target="_blank" rel="noopener">下载 PDF</a></p>
    </article>`).join('');
}

document.addEventListener('DOMContentLoaded', async () => {
  try {
    const payload = await apiJson('/api/v1/summaries?limit=50');
    render(payload.items);
  } catch {
    document.getElementById('summaries').innerHTML = '<p class="error">总结列表加载失败。</p>';
  }
});
