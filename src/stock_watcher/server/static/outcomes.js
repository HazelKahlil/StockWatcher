import { apiJson, esc } from './app.js?v=6';

const rangeLabels = { week: '近 1 周', month: '近 1 月', all: '全部' };

function rate(value) {
  return value == null ? '—' : `${(Number(value) * 100).toFixed(1)}%`;
}

function percent(value) {
  if (value == null) return '—';
  const number = Number(value);
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function price(value) {
  return value == null ? '—' : `¥${Number(value).toFixed(2)}`;
}

function direction(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number === 0) return 'neutral';
  return number > 0 ? 'up' : 'down';
}

function metric(label, value, detail = '') {
  return `<article class="outcome-summary-card"><span>${esc(label)}</span><strong>${esc(value)}</strong>${detail ? `<small>${esc(detail)}</small>` : ''}</article>`;
}

function renderStats(targetId, stats) {
  const target = document.getElementById(targetId);
  target.innerHTML = `
    <dl class="outcome-slot-stats">
      <div><dt>个人胜率</dt><dd>${rate(stats.win_rate)}</dd></div>
      <div><dt>平均收益</dt><dd data-direction="${direction(stats.average_return_pct)}">${percent(stats.average_return_pct)}</dd></div>
      <div><dt>已结算</dt><dd>${stats.settled_count} / ${stats.total_count}</dd></div>
    </dl>`;
}

function renderPortfolios(portfolio) {
  const target = document.getElementById('outcome-portfolios');
  const days = portfolio.days || [];
  if (!days.length) {
    target.innerHTML = '<p class="muted outcome-empty">暂无完整交易日组合。</p>';
    return;
  }
  target.innerHTML = `
    <table>
      <thead><tr><th>入选交易日</th><th>完整度</th><th>等权平均收益</th><th>结果</th></tr></thead>
      <tbody>${days.map((row) => `
        <tr>
          <td>${esc(row.entry_trade_date)}</td>
          <td>${row.settled_count} / ${row.total_count}${row.complete ? '' : ' · 未完整'}</td>
          <td data-direction="${direction(row.average_return_pct)}">${percent(row.average_return_pct)}</td>
          <td>${row.won == null ? '不计入' : (row.won ? '组合胜' : '组合未胜')}</td>
        </tr>`).join('')}</tbody>
    </table>`;
}

function renderRecords(records) {
  const target = document.getElementById('outcome-records');
  if (!records.length) {
    target.innerHTML = '<p class="muted outcome-empty">暂无次日复盘记录；从下一笔固定提醒开始记录。</p>';
    return;
  }
  target.innerHTML = records.map((row) => `
    <article class="outcome-record-card" data-direction="${direction(row.return_pct)}">
      <div class="outcome-record-head">
        <span>${esc(row.entry_trade_date)} · ${esc(row.slot)}</span>
        <strong>TOP ${row.rank}</strong>
      </div>
      <h3>${esc(row.name)} <small>${esc(row.code)}</small></h3>
      <p class="outcome-price-line">${price(row.entry_price)} <span aria-hidden="true">→</span> ${price(row.exit_price)}</p>
      <p class="outcome-return" data-direction="${direction(row.return_pct)}">${percent(row.return_pct)}</p>
      <p class="muted">${esc(row.display_reason)}</p>
    </article>`).join('');
}

function render(payload) {
  const summary = payload.summary;
  document.getElementById('outcome-page-summary').innerHTML = [
    metric('个人胜率', rate(summary.win_rate), `已结算 ${summary.settled_count} 笔`),
    metric('日组合胜率', rate(payload.portfolio.win_rate), `完整组合日 ${payload.portfolio.complete_days} 天`),
    metric('平均收益', percent(summary.average_return_pct), '按已结算候选计算'),
    metric('已结算 / 总数', `${summary.settled_count} / ${summary.total_count}`, '不可验证数据不计入胜率'),
  ].join('');
  renderStats('outcome-morning', payload.morning);
  renderStats('outcome-afternoon', payload.afternoon);
  renderPortfolios(payload.portfolio);
  renderRecords(payload.records || []);
  document.getElementById('outcome-page-backfill').textContent = payload.backfill.message;
  document.getElementById('outcome-page-status').textContent = `${rangeLabels[payload.range]} · 共 ${summary.total_count} 笔理论记录`;
}

async function load(rangeName) {
  const status = document.getElementById('outcome-page-status');
  status.textContent = '正在读取复盘记录…';
  try {
    render(await apiJson(`/api/v1/outcomes?range=${encodeURIComponent(rangeName)}`));
  } catch {
    status.textContent = '复盘暂时无法读取，请稍后重试。';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-outcome-range]').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('[data-outcome-range]').forEach((item) => item.classList.remove('is-active'));
      button.classList.add('is-active');
      void load(button.dataset.outcomeRange);
    });
  });
  void load('month');
});
