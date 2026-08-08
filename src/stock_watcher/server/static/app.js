// Shared StockWatcher Web client: CSRF, fetch helpers, WebSocket, notifications.
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

export function csrf() {
  return csrfToken;
}

export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  if (options.method && options.method !== 'GET' && options.method !== 'HEAD') {
    headers['X-CSRF-Token'] = csrfToken;
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    window.location.assign('/');
    throw new Error('unauthorized');
  }
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.error?.message || message;
    } catch { /* keep default */ }
    const error = new Error(message);
    error.status = response.status;
    error.payload = await response.json().catch(() => null);
    throw error;
  }
  return response;
}

export async function apiJson(path, options = {}) {
  const response = await api(path, options);
  return response.json();
}

let ws = null;
let lastEventId = 0;
const listeners = new Set();

export function onEvent(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function wsState() {
  return ws?.readyState ?? WebSocket.CLOSED;
}

function notifyListeners(event) {
  for (const fn of listeners) {
    try { fn(event); } catch { /* per-listener isolation */ }
  }
}

export function connectEvents() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return ws;
  }
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${protocol}://${window.location.host}/ws/v1/events?after_id=${lastEventId}`);
  const stateEl = document.getElementById('ws-state');
  const setState = (label, cls) => {
    if (!stateEl) return;
    const state = cls === 'healthy' ? 'online' : cls === 'stale' ? 'stale' : 'connecting';
    stateEl.dataset.state = state;
    stateEl.setAttribute('aria-label', label);
    const labelEl = stateEl.querySelector('.status-label');
    if (labelEl) labelEl.textContent = label;
    else stateEl.textContent = label;
  };
  ws.addEventListener('open', () => setState('实时连接在线', 'healthy'));
  ws.addEventListener('close', () => {
    setState('实时连接断开，重连中…', 'stale');
    setTimeout(connectEvents, 3000);
  });
  ws.addEventListener('message', (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch { return; }
    if (event.event_type === 'server.hello') {
      lastEventId = event.payload.latest_event_id || 0;
    } else if (event.event_id > lastEventId) {
      lastEventId = event.event_id;
    }
    notifyListeners(event);
  });
  return ws;
}

export function requestNotificationPermission() {
  if (!('Notification' in window)) return Promise.resolve('unsupported');
  if (Notification.permission === 'granted') return Promise.resolve('granted');
  return Notification.requestPermission();
}

export function notify(title, body) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  new Notification(title, { body });
}

export function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function fmtTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// Logout (CSRF protected)
document.addEventListener('DOMContentLoaded', () => {
  const button = document.getElementById('logout-btn');
  if (button) {
    button.addEventListener('click', async () => {
      try {
        await api('/api/v1/auth/logout', { method: 'POST' });
      } finally {
        window.location.assign('/');
      }
    });
  }
  if (csrfToken) {
    connectEvents();
  }
});
