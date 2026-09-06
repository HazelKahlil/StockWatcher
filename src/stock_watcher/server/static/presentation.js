// Pure display helpers; never infer candidate freshness from scan completion.
export function candidateTimestamp(state) {
  if (!Array.isArray(state.candidates) || !state.candidates.length) return null;
  return [state.source_ts, state.candidate_snapshot_generated_at]
    .find(value => value && Number.isFinite(new Date(value).getTime())) || null;
}

export function shanghaiDay(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(date);
}

export function retainedCandidates(state, now = new Date()) {
  const stamp = candidateTimestamp(state);
  return Boolean(stamp && (state.candidates_source === 'last_realtime_snapshot'
    || state.service_state !== 'healthy' || shanghaiDay(stamp) !== shanghaiDay(now)));
}

export function displayMarketPhase(state, now = new Date()) {
  const weekday = new Intl.DateTimeFormat('en-US', {timeZone: 'Asia/Shanghai', weekday: 'short'}).format(now);
  if (weekday === 'Sat' || weekday === 'Sun') return '周末休市';
  const labels = { preopen: '盘前', morning: '上午盘', lunch: '午休', afternoon: '下午盘', closed: '休市', unknown: '待确认' };
  return labels[state.market_state] || '待确认';
}

export function groupRecords(records, status = 'all', query = '') {
  const search = query.trim().toLowerCase();
  const groups = new Map();
  for (const row of records) {
    if (status !== 'all' && row.status !== status) continue;
    if (search && !`${row.name || ''} ${row.code || ''}`.toLowerCase().includes(search)) continue;
    const date = row.entry_trade_date;
    if (!groups.has(date)) groups.set(date, []);
    groups.get(date).push(row);
  }
  return [...groups.entries()];
}
