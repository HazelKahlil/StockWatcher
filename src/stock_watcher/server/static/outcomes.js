import { apiJson, esc } from './app.js?v=8';

import { enhanceDetails } from './motion.js?v=1';

const expandedDates = new Map();

import { groupRecords } from './presentation.js?v=1';

let currentRecords = [];
let requestController = null;
let requestedRange = 'month';
let loadedRange = null;

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
      <div><dt>个股胜率</dt><dd>${rate(stats.win_rate)}</dd></div>
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
          <td>${row.settled_count} / ${row.total_count} 已结算<small>${row.complete ? '完整 6 笔' : '未形成完整 6 笔组合'}</small></td>
          <td data-direction="${direction(row.average_return_pct)}">${percent(row.average_return_pct)}</td>
          <td>${row.won == null ? '不计入' : (row.won ? '组合胜' : '组合未胜')}</td>
        </tr>`).join('')}</tbody>
    </table>`;
}

function renderRecords() {
  const target = document.getElementById('outcome-records');
  target.querySelectorAll('details[data-date]').forEach(day => expandedDates.set(day.dataset.date, day.querySelector('summary').getAttribute('aria-expanded') === 'true'));
  const groups = groupRecords(currentRecords, document.getElementById('record-status').value, document.getElementById('record-search').value);
  const count = groups.reduce((total, [, rows]) => total + rows.length, 0);
  document.getElementById('record-count').textContent = `显示 ${count} / ${currentRecords.length} 笔 · 明细筛选不改变上方统计范围`;
  if (!groups.length) {
    target.innerHTML = `<div class="empty-state"><strong>${currentRecords.length ? '没有匹配的记录' : '暂无次日复盘记录'}</strong>${currentRecords.length ? '换一个名称、代码或结算状态试试。' : '从下一笔固定时点观察开始记录。'}</div>`;
    return;
  }
  const recordMarkup = row => `
        <article class="outcome-record-card" data-direction="${direction(row.return_pct)}">
          <div class="outcome-record-head"><span>${esc(row.slot)}</span><strong>TOP ${esc(row.rank)}</strong></div>
          <h3>${esc(row.name)} <small>${esc(row.code)}</small></h3>
          <p class="outcome-price-line">${price(row.entry_price)} <span aria-hidden="true">→</span> ${price(row.exit_price)}</p>
          <p class="outcome-return" data-direction="${direction(row.return_pct)}">${percent(row.return_pct)}</p>
          <p class="muted">${esc(row.display_reason)}</p>
        </article>`;
  target.innerHTML = groups.map(([date, records], index) => `
    <details class="outcome-day" data-date="${esc(date)}" ${(expandedDates.get(date) ?? index === 0) ? 'open' : ''}>
      <summary>${esc(date)} <span>${records.length} 笔 · ${records.filter(row => row.status === 'settled').length} 已结算</span></summary>
      <div class="outcome-day-grid"></div>
    </details>`).join('');
  target.querySelectorAll('details').forEach((day, index) => {
    let populated = false;
    const populate = () => {
      if (populated) return;
      populated = true;
      day.querySelector('.outcome-day-grid').innerHTML = groups[index][1].map(recordMarkup).join('');
    };
    if (day.open) populate();
    day.querySelector('summary').addEventListener('click', populate);
    day.addEventListener('toggle', () => { if (day.open) populate(); });
  });
  enhanceDetails(target);
}

function render(payload) {
  const summary = payload.summary;
  document.getElementById('outcome-page-summary').innerHTML = [
    metric('个股胜率', rate(summary.win_rate), `已结算 ${summary.settled_count} 笔`),
    metric('日组合胜率', rate(payload.portfolio.win_rate), `完整组合日 ${payload.portfolio.complete_days} 天`),
    metric('平均收益', percent(summary.average_return_pct), '按已结算候选计算'),
    metric('已结算 / 总数', `${summary.settled_count} / ${summary.total_count}`, `${summary.total_count - summary.settled_count} 笔未结算或不可验证`),
  ].join('');
  renderStats('outcome-morning', payload.morning);
  renderStats('outcome-afternoon', payload.afternoon);
  renderPortfolios(payload.portfolio);
  currentRecords = payload.records || [];
  renderRecords();
  document.getElementById('outcome-page-backfill').textContent = payload.backfill.message;
  document.getElementById('outcome-page-status').textContent = `${rangeLabels[payload.range]} · 共 ${summary.total_count} 笔理论记录`;
}

async function load(rangeName) {
  requestController?.abort();
  const controller = new AbortController();
  requestController = controller;
  requestedRange = rangeName;
  const status = document.getElementById('outcome-page-status');
  const page = document.querySelector('.outcome-page');
  page.setAttribute('aria-busy', 'true');
  status.dataset.error = 'false';
  status.textContent = `正在读取${rangeLabels[rangeName]}复盘…`;
  document.getElementById('outcome-retry').hidden = true;
  try {
    const payload = await apiJson(`/api/v1/outcomes?range=${encodeURIComponent(rangeName)}`, {signal:controller.signal});
    if (controller.signal.aborted) return;
    render(payload);
    loadedRange = rangeName;
    document.querySelectorAll('[data-outcome-range]').forEach(button => {
      const active = button.dataset.outcomeRange === rangeName;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
  } catch {
    if (controller.signal.aborted) return;
    status.dataset.error = 'true';
    status.textContent = `读取失败，${loadedRange ? `仍显示${rangeLabels[loadedRange]}的上次结果` : '暂时没有可展示的结果'}。请重试。`;
    document.getElementById('outcome-retry').hidden = false;
  } finally {
    if (requestController === controller) page.setAttribute('aria-busy', 'false');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-outcome-range]').forEach(button => {
    button.addEventListener('click', () => void load(button.dataset.outcomeRange));
  });
  document.getElementById('record-search').addEventListener('input', renderRecords);
  document.getElementById('record-status').addEventListener('change', renderRecords);
  document.getElementById('outcome-retry').addEventListener('click', () => void load(requestedRange));
  void load('month');
});
