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

export function placeholderCardHTML(rank) {
  return `
  <article class="card placeholder-card" aria-label="等待抓取第 ${rank} 只候选">
    <div class="candidate-main">
      <span class="rank rank-${rank}-badge">${rank}</span>
      <div class="candidate-identity">
        <h3 class="display-name placeholder-text">等待候选</h3>
        <span class="display-code placeholder-text">------</span>
      </div>
      <span class="level-tag level-placeholder">待</span>
    </div>
    <div class="candidate-quote">
      <span class="ashare-pct placeholder-text">--.--%</span>
      <span class="ashare-price placeholder-text">¥--.--</span>
    </div>
    <div class="candidate-meta">
      <strong class="placeholder-text">板块待抓取</strong>
      <small class="placeholder-card-status">正在抓取</small>
    </div>
    <span class="card-arrow" aria-hidden="true">›</span>
  </article>`;
}

export function candidateCardHTML(candidate, state) {
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

  return `
  <article class="card ${rank === 1 ? 'rank-1-card' : ''}" data-detail-code="${esc(candidate.code)}">
    <div class="candidate-main">
      <span class="rank rank-${rank}-badge">${rank}</span>
      <div class="candidate-identity">
        <h3 class="display-name">${esc(candidate.name)}</h3>
        <span class="display-code">${esc(candidate.code)}</span>
      </div>
      <span class="level-tag level-${level.tone}">${esc(level.label.replace('级', ''))}</span>
    </div>
    <div class="candidate-quote">
      <span class="ashare-pct" data-direction="${direction}">${pctStr}</span>
      <span class="ashare-price">¥${price}</span>
    </div>
    <div class="candidate-meta">
      <strong>${esc(candidate.sector_name || '板块待确认')}</strong>
      <small>${esc(observationLabel)}</small>
    </div>
    <button type="button" class="card-open-detail" data-detail="${esc(candidate.code)}" aria-label="查看 ${esc(candidate.name)} 候选详情"><span aria-hidden="true">›</span></button>
  </article>`;
}
