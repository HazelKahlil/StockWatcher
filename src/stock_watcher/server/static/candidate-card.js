// Shared Top3 card markup for the live dashboard and isolated preview.

export function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function levelMeta(candidate) {
  const raw = String(candidate.level || '');
  if (raw.includes('强')) return { label: '强级', tone: 'strong' };
  if (raw.includes('中')) return { label: '中级', tone: 'medium' };
  return { label: candidate.is_formal ? '近级' : '近级补位', tone: 'near' };
}

function cardShell({
  rank,
  nameHtml,
  codeHtml,
  quoteHtml,
  levelHtml,
  metaHtml,
  detailHtml,
  articleClass,
  ariaLabel,
  detailCode,
  approval,
}) {
  const codeAttr = detailCode ? ` data-detail-code="${esc(detailCode)}"` : '';
  const labelAttr = ariaLabel ? ` aria-label="${esc(ariaLabel)}"` : '';
  return `
  <article class="card ${articleClass}"${codeAttr}${labelAttr}>
    <div class="candidate-primary">
      <span class="rank rank-${rank}-badge">${rank}</span>
      <div class="candidate-identity">
        <h3 class="display-name">${nameHtml}</h3>
        <span class="display-code">${codeHtml}</span>
      </div>
    </div>
    <div class="candidate-quote">${quoteHtml}</div>
    <div class="candidate-aux">
      ${levelHtml}
      ${metaHtml}
    </div>
    ${approval ? approvalActions(approval, detailHtml) : detailHtml}
  </article>`;
}

export function placeholderCardHTML(rank) {
  return cardShell({
    rank,
    articleClass: 'placeholder-card',
    ariaLabel: `等待抓取第 ${rank} 只候选`,
    nameHtml: '<span class="placeholder-text">等待候选</span>',
    codeHtml: '<span class="placeholder-text">------</span>',
    quoteHtml: (
      '<span class="ashare-pct placeholder-text">--.--%</span>'
      + '<span class="ashare-price placeholder-text">¥--.--</span>'
    ),
    levelHtml: '<span class="level-tag level-placeholder">待</span>',
    metaHtml: (
      '<strong class="placeholder-text">板块待抓取</strong>'
      + '<small class="placeholder-card-status">正在抓取</small>'
    ),
    detailHtml: '<span class="card-arrow" aria-hidden="true">›</span>',
  });
}


function approvalActions(approval, detailHtml) {
  const { candidate, state } = approval;
  const snapshotId = Number(state?.snapshot_id);
  if (!Number.isSafeInteger(snapshotId) || snapshotId <= 0) return detailHtml;
  return `<div class="candidate-actions">
    <div class="candidate-approval" data-approval-control
         data-approval-code="${esc(candidate.code)}"
         data-approval-name="${esc(candidate.name || candidate.code)}"
         data-approval-snapshot="${snapshotId}">
      <label><input type="checkbox" data-approval-checkbox disabled
        aria-label="选择 ${esc(candidate.name || candidate.code)}">
        <span data-approval-label>选择</span></label>
      <button type="button" data-approval-retry hidden>重试保存</button>
    </div>${detailHtml}</div>`;
}

export function candidateCardHTML(candidate, state, options = {}) {
  const level = levelMeta(candidate);
  const price = Number(candidate.price).toFixed(2);
  const changePct = Number(candidate.change_pct);
  const pctStr = (changePct > 0 ? '+' : '') + changePct.toFixed(2) + '%';
  const direction = changePct > 0 ? 'up' : (changePct < 0 ? 'down' : 'neutral');
  const fundLabel = state?.fund_module && state.fund_module !== 'unavailable'
    ? '资金增强可用'
    : '资金未确认';
  const observationLabel = candidate.is_formal ? fundLabel : `补位观察 · ${fundLabel}`;
  const rank = Number(candidate.rank) || 1;
  const name = candidate.name || '待确认';
  const code = candidate.code || '';

  return cardShell({
    rank,
    articleClass: rank === 1 ? 'rank-1-card' : '',
    approval: options.approvalsEnabled ? { candidate, state } : null,
    detailCode: code,
    nameHtml: esc(name),
    codeHtml: esc(code),
    quoteHtml: (
      `<span class="ashare-pct" data-direction="${direction}">${esc(pctStr)}</span>`
      + `<span class="ashare-price">¥${esc(price)}</span>`
    ),
    levelHtml: `<span class="level-tag level-${level.tone}">${esc(level.label.replace('级', ''))}</span>`,
    metaHtml: (
      `<strong>${esc(candidate.sector_name || '板块待确认')}</strong>`
      + `<small>${esc(observationLabel)}</small>`
    ),
    detailHtml: (
      `<button type="button" class="card-open-detail" data-detail="${esc(code)}"`
      + ` aria-label="查看 ${esc(name)} 候选详情">`
      + '<span aria-hidden="true">›</span></button>'
    ),
  });
}
