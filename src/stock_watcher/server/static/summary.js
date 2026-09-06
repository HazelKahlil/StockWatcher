import { api, apiJson, esc, fmtTime } from './app.js?v=7';

async function loadBody(panel, date) {
  const body = panel.querySelector('.report-body');
  if (panel.dataset.loaded === 'true' || panel.dataset.loading === 'true') return;
  panel.dataset.loading = 'true';
  body.textContent = '正在读取总结正文…';
  body.setAttribute('aria-busy', 'true');
  try {
    const payload = await apiJson(`/api/v1/summaries/${encodeURIComponent(date)}`);
    body.textContent = payload.summary_text || '本日尚无总结正文。';
    panel.dataset.loaded = 'true';
  } catch {
    body.innerHTML = '<p>正文暂时无法读取。</p><button type="button" class="button-secondary">重新加载</button>';
    body.querySelector('button').addEventListener('click', () => void loadBody(panel, date));
  } finally {
    panel.dataset.loading = 'false';
    body.setAttribute('aria-busy', 'false');
  }
}

async function download(button, date) {
  const status = button.closest('.report-card').querySelector('.report-status');
  button.disabled = true;
  button.textContent = '准备下载…';
  status.textContent = '';
  try {
    const response = await api(`/api/v1/summaries/${encodeURIComponent(date)}/pdf`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `stockwatcher-${date}.pdf`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch {
    status.textContent = 'PDF 暂不可用，可先阅读总结正文，稍后再试。';
  } finally {
    button.disabled = false;
    button.textContent = '下载 PDF';
  }
}

function render(items) {
  const wrap = document.getElementById('summaries');
  if (!items.length) {
    wrap.innerHTML = '<div class="empty-state"><strong>暂无盘后总结</strong>交易日 15:30 后生成，可稍后回来查看。</div>';
    return;
  }
  wrap.innerHTML = items.map((item, index) => `
    <article class="report-card">
      <div class="report-header"><div>
        <h2><time datetime="${esc(item.trade_date)}">${esc(item.trade_date)}</time>${index === 0 ? '<span class="report-badge">最近一期</span>' : ''}${item.catch_up ? '<span class="report-badge">补生成</span>' : ''}</h2>
        <p>${esc(item.alert_count)} 次观察提醒 · 生成于 ${fmtTime(item.generated_at)}</p>
      </div><button type="button" class="button-secondary" data-download="${esc(item.trade_date)}">下载 PDF</button></div>
      <p class="report-status" role="status"></p>
      <details class="report-detail" data-date="${esc(item.trade_date)}"><summary>阅读总结</summary><div class="report-body" aria-live="polite"></div></details>
      <details class="report-diagnostics"><summary>报告信息</summary><p>来源版本：${esc(item.version || '未记录')}</p><a href="/api/v1/summaries/${esc(item.trade_date)}" target="_blank" rel="noopener">查看原始 JSON</a></details>
    </article>`).join('');
  wrap.querySelectorAll('[data-date]').forEach(panel => panel.addEventListener('toggle', () => {
    if (panel.open) void loadBody(panel, panel.dataset.date);
  }));
  wrap.querySelectorAll('[data-download]').forEach(button => button.addEventListener('click', () => void download(button, button.dataset.download)));
}

async function load() {
  const status = document.getElementById('summary-status');
  const retry = document.getElementById('summary-retry');
  retry.hidden = true;
  status.dataset.error = 'false';
  status.textContent = '正在读取总结…';
  try {
    const payload = await apiJson('/api/v1/summaries?limit=50');
    render(payload.items);
    status.textContent = `最近一个月 · ${payload.items.length} 份总结`;
  } catch {
    status.dataset.error = 'true';
    status.textContent = '总结列表加载失败，请重试。';
    retry.hidden = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('summary-retry').addEventListener('click', () => void load());
  void load();
});
